import re
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.agents.llm import get_llm
from app.rag.knowledge_base import get_lending_store, get_campaign_template_store
from app.store.db import db

LOAN_SYSTEM_PROMPT = """You are the Financial Nudge Agent inside Vriddhi, an AI growth copilot for Paytm merchants. \
You are given exact, already-computed loan eligibility numbers (tier, amounts, rates — never yours to recalculate) \
plus retrieved policy passages from lending_eligibility_rules.json. Write a customer-facing offer message and a \
short merchant-facing reasoning. Use the exact numbers given — never invent or change them."""

INSURANCE_SYSTEM_PROMPT = """You are the Financial Nudge Agent inside Vriddhi. You are given an insurance product's \
exact eligibility and premium (never yours to recalculate) plus a retrieved policy passage. Write a short \
customer-facing offer message and a 1-sentence merchant-facing reasoning citing the eligibility rule. Use the exact \
numbers given — never invent or change them."""


class LoanOfferDraft(BaseModel):
    message: str = Field(description="Customer-facing loan offer message, following the retrieved template's tone, using the exact tier/amount/rate/tenure given")
    reasoning: str = Field(description="1-2 sentence merchant-facing explanation of why this tier/offer was chosen, citing the retrieved policy")


class InsuranceOfferDraft(BaseModel):
    message: str = Field(description="Short customer-facing insurance offer message using the exact product name and premium given")
    reasoning: str = Field(description="1 sentence explanation citing the retrieved eligibility rule")


def _months_between(date_str, now):
    then = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return (now - then).total_seconds() / 86400 / 30


def run_financial_agent(merchant, data, tracer):
    """Evaluates working-capital loan eligibility against
    lending_eligibility_rules.json. Eligibility math and the auto-approve
    vs. escalate prediction are deterministic — re-checked and enforced
    again at apply time by the guardrails layer, since that is a
    state-changing action and must never trust an LLM's read of the rules.
    The LLM's job is drafting the offer, grounded in retrieved policy text."""
    rules = data.lending_rules
    wc = rules["working_capital_loan"]
    now = datetime.now(timezone.utc)
    months_on_platform = _months_between(merchant["onboarded_date"], now)

    tracer.log(
        "financial_agent",
        f"Checking working-capital eligibility for {merchant['merchant_id']}: {months_on_platform:.1f} months on platform, "
        f"avg monthly settlement ₹{merchant['avg_monthly_settlement_inr']}, consistency score {merchant['settlement_consistency_score']}.",
    )

    eligible = (
        months_on_platform >= wc["eligibility_criteria"]["min_months_on_platform"]
        and merchant["avg_monthly_settlement_inr"] >= wc["eligibility_criteria"]["min_avg_monthly_settlement_inr"]
        and merchant["settlement_consistency_score"] >= wc["eligibility_criteria"]["min_settlement_consistency_score"]
    )
    if not eligible:
        tracer.log("financial_agent", "Merchant does not meet base eligibility criteria in lending_eligibility_rules.json — no offer surfaced.")
        return None

    tier = None
    for t in sorted(wc["tiers"], key=lambda t: -t["min_avg_monthly_settlement_inr"]):
        if merchant["avg_monthly_settlement_inr"] >= t["min_avg_monthly_settlement_inr"]:
            tier = t
            break
    if not tier:
        tracer.log("financial_agent", "Merchant is eligible but does not clear any tier's settlement floor — no offer surfaced.")
        return None

    suggested_apply_amount = min(tier["max_loan_inr"], wc["auto_approve_threshold_inr"])
    will_escalate = merchant["settlement_consistency_score"] < 0.8 or suggested_apply_amount > wc["escalate_to_relationship_manager_above_inr"]

    lending_hits = get_lending_store(data).similarity_search(f"{tier['tier']} tier working capital loan escalation threshold auto approve", k=2)
    template_hits = get_campaign_template_store(data).similarity_search("loan eligibility nudge offer message", k=1)
    retrieved = "\n".join(d["text"] for d, _ in lending_hits) + ("\n" + template_hits[0][0]["text"] if template_hits else "")
    for d, score in lending_hits:
        tracer.log("financial_agent", f'RAG retrieval: matched "{d["id"]}" (cosine similarity {score:.3f})')

    prompt = (
        f"Merchant: {merchant['name']}. Tier: {tier['tier']}. Max loan: ₹{tier['max_loan_inr']:,}. "
        f"Interest rate: {tier['indicative_interest_rate_pct']}%. Tenure: {tier['tenure_months']} months. "
        f"Suggested draw amount: ₹{suggested_apply_amount:,}. Predicted path: {'will need relationship-manager review' if will_escalate else 'auto-approvable'}.\n\n"
        f'Retrieved policy passages:\n"{retrieved}"'
    )
    draft = get_llm().with_structured_output(LoanOfferDraft).invoke(
        [{"role": "system", "content": LOAN_SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    )
    tracer.log("financial_agent", f"Drafted loan offer: {draft.reasoning}", {"tier": tier["tier"], "willEscalate": will_escalate})

    return {
        "id": f"REC-LOAN-{merchant['merchant_id']}",
        "type": "loan",
        "merchantId": merchant["merchant_id"],
        "tier": tier["tier"],
        "maxLoanInr": tier["max_loan_inr"],
        "suggestedApplyAmount": suggested_apply_amount,
        "interestRatePct": tier["indicative_interest_rate_pct"],
        "tenureMonths": tier["tenure_months"],
        "willEscalate": will_escalate,
        "message": draft.message,
        "reasoning": draft.reasoning,
    }


def _parse_min_months(eligibility_text):
    match = re.search(r"min\s+(\d+)\s+month", eligibility_text, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def run_insurance_check(merchant, data, tracer):
    """Evaluates the merchant_insurance products in
    lending_eligibility_rules.json — present in the dataset but unused
    until this agent. Eligibility math is deterministic; the LLM drafts
    the offer, grounded in a retrieved policy passage."""
    rules = data.lending_rules
    now = datetime.now(timezone.utc)
    months_on_platform = _months_between(merchant["onboarded_date"], now)
    enrolled = {e["productName"] for e in db.get_insurance_enrollments(merchant["merchant_id"])}

    tracer.log("financial_agent", f"Checking merchant_insurance eligibility for {merchant['merchant_id']}: {months_on_platform:.1f} months on platform.")

    candidate = None
    for p in rules["merchant_insurance"]["products"]:
        if months_on_platform >= _parse_min_months(p["eligibility"]) and p["name"] not in enrolled:
            candidate = p
            break

    if not candidate:
        tracer.log("financial_agent", "No new insurance product to surface — either not yet eligible or already enrolled in everything eligible.")
        return None

    hits = get_lending_store(data).similarity_search(f"{candidate['name']} insurance eligibility premium", k=1)
    retrieved = hits[0][0]["text"] if hits else candidate["eligibility"]
    if hits:
        tracer.log("financial_agent", f'RAG retrieval: matched "{hits[0][0]["id"]}" (cosine similarity {hits[0][1]:.3f})')

    prompt = f"Product: {candidate['name']}. Premium: ₹{candidate['indicative_annual_premium_inr']}/year.\n\nRetrieved passage:\n\"{retrieved}\""
    draft = get_llm().with_structured_output(InsuranceOfferDraft).invoke(
        [{"role": "system", "content": INSURANCE_SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    )
    tracer.log("financial_agent", f"Drafted insurance offer: {draft.reasoning}", {"productName": candidate["name"]})

    return {
        "id": f"REC-INSURANCE-{merchant['merchant_id']}-{candidate['name'].replace(' ', '_')}",
        "type": "insurance",
        "merchantId": merchant["merchant_id"],
        "productName": candidate["name"],
        "premiumInr": candidate["indicative_annual_premium_inr"],
        "message": draft.message,
        "reasoning": draft.reasoning,
    }
