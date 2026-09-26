from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from app.agents.llm import get_llm
from app.rag.knowledge_base import get_campaign_template_store

SYSTEM_PROMPT = """You are the Growth Action Agent inside Vriddhi, an AI growth copilot for Paytm merchants. Given \
a diagnosis (why a promo is needed), a chosen catalog item, and a fixed discount percentage that has already \
passed guardrail checks, draft a concrete customer-facing promo message and a short merchant-facing explanation.

Rules:
- Use the EXACT discount percentage, item name, and expiry date you are given — never change, round, or invent a \
different number. The discount is a guardrail-enforced value; altering it would bypass a safety check.
- Follow the tone and structure of the retrieved template passage you're given (a WhatsApp-style message with a \
greeting, the offer, and a call to action).
- The reasoning should be 1-2 plain-language sentences explaining to the merchant WHY this item/discount was chosen."""


class PromoDraft(BaseModel):
    message: str = Field(description="Customer-facing WhatsApp promo message, following the retrieved template's tone, using the exact discount/item/expiry given")
    reasoning: str = Field(description="1-2 sentence merchant-facing explanation of why this promo was drafted")


def _add_days(date_str, days):
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=days)
    return dt.strftime("%Y-%m-%d")


def _compute_as_of_date(transactions):
    if not transactions:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    latest = max(datetime.strptime(t["date"], "%Y-%m-%d") for t in transactions)
    return latest.strftime("%Y-%m-%d")


def _draft_promo(merchant, item, discount_pct, max_discount_pct, customer_segment, expiry_date, situation, retrieval_query, rec_id, extra_fields, data, tracer):
    hits = get_campaign_template_store(data).similarity_search(retrieval_query, k=1)
    if hits:
        retrieved_doc, score = hits[0]
        retrieved_text = retrieved_doc["text"]
        tracer.log("growth_action_agent", f'RAG retrieval: query="{retrieval_query}" → matched "{retrieved_doc["id"]}" (cosine similarity {score:.3f})')
    else:
        retrieved_text = "No template passage retrieved."
        tracer.log("growth_action_agent", "RAG retrieval returned no matches.")

    prompt = (
        f"Merchant: {merchant['name']} ({merchant['category']}).\n"
        f"Situation: {situation}\n"
        f"Chosen item: {item['item_name']}.\n"
        f"Discount to apply: {discount_pct}% (guardrail cap {max_discount_pct}% — do not exceed or alter this number).\n"
        f"Offer expires: {expiry_date}.\n"
        f"Target segment: {customer_segment}.\n\n"
        f'Retrieved template/guardrails passage:\n"{retrieved_text}"\n\n'
        "Draft the customer-facing promo message and a short merchant-facing reasoning."
    )
    draft = get_llm().with_structured_output(PromoDraft).invoke(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    )
    tracer.log("growth_action_agent", f"Drafted promo for {item['item_name']}: {draft.message}")

    return {
        "id": rec_id,
        "type": "promo",
        "merchantId": merchant["merchant_id"],
        "itemId": item["item_id"],
        "itemName": item["item_name"],
        "discountPct": discount_pct,
        "maxDiscountPct": max_discount_pct,
        "customerSegment": customer_segment,
        "templateId": "sales_dip_recovery_promo",
        "expiryDate": expiry_date,
        "message": draft.message,
        "reasoning": draft.reasoning,
        **extra_fields,
    }


def run_growth_action_agent(merchant, diagnosis, catalog, transactions, data, tracer):
    """Given an insight-agent diagnosis, drafts a concrete promo against a
    real catalog item — it only drafts, the orchestrator calls the mock
    campaign API after the merchant approves the card in the UI. The item
    choice, discount cap, and expiry are all deterministic/guardrail-safe;
    the LLM's job is turning that into a grounded, well-written message."""
    if not diagnosis or not diagnosis.get("has_anomaly"):
        return None
    if not catalog:
        tracer.log("growth_action_agent", "Diagnosis received but merchant has no catalog items to promote — skipping.")
        return None

    tracer.log("growth_action_agent", f"Received diagnosis from insight agent ({diagnosis['consecutive_weeks_flagged']} weeks flagged). Selecting a catalog item to drive footfall.")
    top_seller = max(catalog, key=lambda c: c["units_sold_last_30d"])
    discount_pct = 12
    max_discount_pct = 15
    customer_segment = "lapsed_weekday_shoppers"
    expiry_date = _add_days(diagnosis["as_of_date"], 5)

    situation = (
        f"Weekday sales are {diagnosis['pct_below_baseline']}% below baseline for {diagnosis['consecutive_weeks_flagged']} "
        f"consecutive weeks: {diagnosis.get('narrative', '')}"
    )

    return _draft_promo(
        merchant, top_seller, discount_pct, max_discount_pct, customer_segment, expiry_date, situation,
        "weekday sales dip recovery discount promo message to lapsed customers",
        f"REC-PROMO-{merchant['merchant_id']}-{top_seller['item_id']}",
        {}, data, tracer,
    )


def draft_slow_mover_boost(merchant, catalog, transactions, data, tracer):
    """Separate from the dip-driven promo above: looks for a single item
    that isn't selling relative to the rest of the catalog — a
    merchandising problem, not a sales-pattern anomaly — and drafts a
    clearance-style discount. Runs regardless of the weekday-dip result."""
    if len(catalog) < 3:
        tracer.log("growth_action_agent", "Catalog too small to meaningfully compare item performance — skipping slow-mover check.")
        return None

    avg_units = sum(c["units_sold_last_30d"] for c in catalog) / len(catalog)
    if avg_units <= 0:
        return None

    slowest = min(catalog, key=lambda c: c["units_sold_last_30d"])
    ratio = slowest["units_sold_last_30d"] / avg_units

    if ratio > 0.4:
        tracer.log(
            "growth_action_agent",
            f"Checked catalog for underperforming items: {slowest['item_name']} is your slowest at "
            f"{slowest['units_sold_last_30d']} units/30d, still within range of your ~{avg_units:.0f} average — "
            "nothing standing out as a slow mover right now.",
        )
        return None

    discount_pct = 15
    customer_segment = f"slow_mover_{slowest['item_id']}"
    as_of_date = _compute_as_of_date(transactions)
    expiry_date = _add_days(as_of_date, 7)
    situation = f"{slowest['item_name']} sold only {slowest['units_sold_last_30d']} units in the last 30 days, well below the catalog average of ~{avg_units:.0f}."

    return _draft_promo(
        merchant, slowest, discount_pct, 15, customer_segment, expiry_date, situation,
        "clearance discount promo for a slow-moving underperforming item",
        f"REC-MERCH-{merchant['merchant_id']}-{slowest['item_id']}",
        {"type": "merchandising", "unitsSold30d": slowest["units_sold_last_30d"]}, data, tracer,
    )
