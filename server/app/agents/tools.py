from langchain_core.tools import tool

from app.agents.tracer import Tracer
from app.agents.insight_agent import run_insight_agent
from app.agents.growth_action_agent import run_growth_action_agent, draft_slow_mover_boost
from app.agents.ops_agent import run_ops_agent, apply_stock_sold, apply_stock_restocked
from app.agents.festival_agent import plan_festival_readiness
from app.agents.financial_agent import run_financial_agent, run_insurance_check
from app.agents.service_agent import run_service_agent
from app.agents.support_agent import run_support_agent
from app.services.catalog_service import get_effective_catalog
from app.services.actions_service import (
    send_campaign_action,
    create_reorder_action,
    apply_loan_action,
    apply_insurance_action,
    dismiss_recommendation_action,
    end_recommendation_action,
)
from app.store.db import db

TYPE_LABEL = {
    "promo": "growth promo",
    "reorder": "restock reorder",
    "loan": "loan offer",
    "festival_prep": "festival stock-up",
    "feedback_digest": "feedback digest",
    "merchandising": "slow-mover promo",
    "insurance": "insurance offer",
}


def _execute_approve(target, data):
    t = target["type"]
    if t in ("promo", "merchandising"):
        return send_campaign_action(target["merchantId"], target["templateId"], target["customerSegment"], target["discountPct"], target["itemId"], rec_id=target["id"], rec_type=t)
    if t == "reorder":
        return create_reorder_action(target["merchantId"], target["itemId"], target["recommendedQty"], rec_id=target["id"])
    if t == "festival_prep":
        return create_reorder_action(target["merchantId"], target["itemId"], target["recommendedQty"], rec_id=target["id"], trigger="festival_prep")
    if t == "loan":
        return apply_loan_action(
            target["merchantId"], target["tier"], target["suggestedApplyAmount"], True,
            data.lending_rules, data.merchants_by_id[target["merchantId"]], rec_id=target["id"],
        )
    if t == "insurance":
        return apply_insurance_action(target["merchantId"], target["productName"], target["premiumInr"], True, rec_id=target["id"])
    return {"status": "blocked", "auditEntry": {"reason": "Unknown recommendation type."}}


def _describe_approve_result(target, result):
    label = TYPE_LABEL.get(target["type"], target["type"])
    status = result["status"]
    if status == "blocked":
        return f"Blocked by guardrail — {result['auditEntry']['reason']}"
    if status == "sent":
        segment = target["customerSegment"].replace("_", " ")
        return f"Sent the {target['discountPct']}% off {target['itemName']} promo to the {segment} segment."
    if status == "created":
        return f"Placed a reorder for {target['recommendedQty']} units of {target['itemName']}."
    if status == "approved":
        return f"Auto-approved the {target['tier']} tier loan of up to ₹{target['suggestedApplyAmount']:,}."
    if status == "escalated":
        return f"Submitted, but needs relationship-manager review first ({'; '.join(result['reasons'])})."
    if status == "enrolled":
        return f"Enrolled in {target['productName']} at ₹{target['premiumInr']:,}/year."
    return f"{label} processed."


def build_scan_tools(merchant, data, collected, catalog=None):
    """Read-only diagnostic tools — every one of these is a deterministic
    function grounded in the actual dataset (transactions.csv,
    category_benchmarks.json, catalog.csv, lending_eligibility_rules.json)
    merged with any merchant-reported stock changes. The LLM decides WHICH
    of these to call and in what order; it never invents the numbers
    itself — that's the job of the underlying agent functions. `catalog`
    can be passed in (build_chat_tools does this) so the stock-update
    tools and these diagnostic tools share the same in-memory list and
    stay consistent within one turn; when called standalone (the
    scheduled scan has no stock-update tools) it's fetched fresh here."""
    merchant_id = merchant["merchant_id"]
    transactions = data.transactions_by_merchant.get(merchant_id, [])
    if catalog is None:
        catalog = get_effective_catalog(data, merchant_id)
    feedback = data.feedback_by_merchant.get(merchant_id, [])
    benchmark = data.category_benchmarks.get(merchant["category"])

    def _record(rec):
        """Append to this request's response payload AND persist to the
        server-side pending-recommendations store immediately — a card
        drafted through chat must be findable by approve_recommendation /
        dismiss_recommendation right away, in this turn or a later one,
        not just reflected in this one response."""
        collected["recommendations"].append(rec)
        db.add_recommendation_to_last_scan(merchant_id, rec)

    @tool
    def check_sales_pattern() -> str:
        """Analyze the merchant's transaction history against their category benchmark to detect a sustained weekday sales dip or other anomaly. Call this first, before draft_dip_recovery_promo, to understand the merchant's sales health."""
        tracer = Tracer()
        diagnosis = run_insight_agent(merchant, transactions, benchmark, data, tracer)
        collected["trace"].extend(tracer.entries)
        collected["diagnosis"] = diagnosis
        if diagnosis.get("has_anomaly"):
            causes = ", ".join(diagnosis["possible_causes"])
            return (
                f"ANOMALY DETECTED: weekday revenue is {diagnosis['pct_below_baseline']}% below baseline "
                f"(₹{diagnosis['baseline_weekday_avg']} -> ₹{diagnosis['recent_weekday_avg']}/day) for "
                f"{diagnosis['consecutive_weeks_flagged']} consecutive weeks. Possible causes: {causes}."
            )
        return "No sales anomaly detected — weekday/weekend pattern is within normal range for this category."

    @tool
    def draft_dip_recovery_promo() -> str:
        """Draft a discount promo on the merchant's top-selling item to recover from a detected weekday sales dip. Only call this AFTER check_sales_pattern has confirmed an anomaly — it will refuse otherwise."""
        diagnosis = collected.get("diagnosis")
        if not diagnosis or not diagnosis.get("has_anomaly"):
            return "Cannot draft a dip-recovery promo: no confirmed sales anomaly. Call check_sales_pattern first."
        tracer = Tracer()
        rec = run_growth_action_agent(merchant, diagnosis, catalog, transactions, data, tracer)
        collected["trace"].extend(tracer.entries)
        if rec:
            _record(rec)
            return f"Drafted: {rec['discountPct']}% off {rec['itemName']}, expires {rec['expiryDate']}. Added to the recommendation feed."
        return "Could not draft a promo — merchant has no catalog items."

    @tool
    def check_slow_movers() -> str:
        """Compare catalog items against each other to find one significantly underperforming (a merchandising problem), independent of any sales-pattern anomaly, and draft a clearance promo for it."""
        tracer = Tracer()
        rec = draft_slow_mover_boost(merchant, catalog, transactions, data, tracer)
        collected["trace"].extend(tracer.entries)
        if rec:
            _record(rec)
            return f"{rec['itemName']} is underperforming ({rec['unitsSold30d']} units/30d) — drafted a {rec['discountPct']}% clearance promo."
        return "No catalog item stands out as a slow mover right now."

    @tool
    def check_stock_levels() -> str:
        """Scan the catalog for items close to running out of stock and draft reorder recommendations with days-to-stockout math."""
        tracer = Tracer()
        recs = run_ops_agent(merchant, catalog, data, tracer)
        collected["trace"].extend(tracer.entries)
        for r in recs:
            _record(r)
        if recs:
            return "Low stock: " + "; ".join(f"{r['itemName']} (~{r['daysToStockout']}d to stockout, reorder {r['recommendedQty']})" for r in recs)
        return "All catalog items are healthily stocked."

    @tool
    def check_festival_readiness(festival_name: str = "") -> str:
        """Check whether the merchant should stock up ahead of an upcoming or ongoing seasonal peak (a festival, wedding season, etc.), independent of the normal reorder threshold. Pass a specific festival name if the merchant mentioned one (e.g. 'diwali'), otherwise leave blank to check the nearest upcoming peak."""
        tracer = Tracer()
        hint = festival_name.strip() or None
        plan = plan_festival_readiness(merchant, catalog, benchmark, transactions, data, tracer, festival_hint=hint)
        collected["trace"].extend(tracer.entries)
        if not plan:
            return "No upcoming seasonal peak within the next 60 days for this category."
        if plan.get("no_match"):
            return f"'{festival_name}' isn't a listed seasonal peak for this merchant's category, so I can't size a stock-up plan for it confidently."
        if not plan.get("has_recommendation"):
            timing = "already underway" if plan["peak"]["window"]["status"] == "ongoing" else f"{plan['peak']['window']['days_until_start']} days away"
            return f"{plan['peak']['label']} is {timing}, but current stock on top sellers already covers the estimated demand."
        rec = plan["recommendation"]
        _record(rec)
        return f"{rec['message']} Added to the recommendation feed."

    @tool
    def check_loan_eligibility() -> str:
        """Check the merchant's eligibility for a working-capital loan against lending_eligibility_rules.json."""
        tracer = Tracer()
        rec = run_financial_agent(merchant, data, tracer)
        collected["trace"].extend(tracer.entries)
        if rec:
            _record(rec)
            path = "will need relationship-manager review" if rec["willEscalate"] else "auto-approvable"
            return f"Eligible for {rec['tier']} tier loan up to ₹{rec['maxLoanInr']:,} ({path})."
        return "Not eligible for a working-capital loan right now."

    @tool
    def check_insurance_eligibility() -> str:
        """Check the merchant's eligibility for Shop Protect or Health Cover insurance products."""
        tracer = Tracer()
        rec = run_insurance_check(merchant, data, tracer)
        collected["trace"].extend(tracer.entries)
        if rec:
            _record(rec)
            return f"Eligible for {rec['productName']} at ₹{rec['premiumInr']}/year."
        return "Not eligible for any new insurance product right now (or already enrolled in everything eligible)."

    @tool
    def summarize_feedback() -> str:
        """Summarize the merchant's recent customer feedback into a short digest."""
        tracer = Tracer()
        rec = run_service_agent(merchant, feedback, data, tracer)
        collected["trace"].extend(tracer.entries)
        if rec:
            _record(rec)
            return rec["message"]
        return "No feedback recorded yet."

    return [
        check_sales_pattern,
        draft_dip_recovery_promo,
        check_slow_movers,
        check_stock_levels,
        check_festival_readiness,
        check_loan_eligibility,
        check_insurance_eligibility,
        summarize_feedback,
    ]


def _resolve_target(merchant_id, recommendation_type, action_verb):
    """Shared targeting logic for approve/dismiss: exact type match first;
    if the type doesn't match anything (the LLM guessed a type string that
    isn't actually pending) but there's exactly one pending item, that's
    almost certainly what the merchant meant — use it rather than failing.
    Otherwise, list what's ACTUALLY pending so the model can retry with a
    valid type instead of getting a dead-end error."""
    pending = db.get_pending_recommendations(merchant_id)
    if not pending:
        return None, "There's nothing pending right now to " + action_verb + "."

    target = None
    if recommendation_type:
        target = next((r for r in pending if r["type"] == recommendation_type), None)

    if not target:
        if len(pending) == 1:
            target = pending[0]
        else:
            labels = ", ".join(f"{TYPE_LABEL.get(r['type'], r['type'])} ({r['type']})" for r in pending)
            return None, f"Ask the merchant which one they mean — currently pending: {labels}."

    return target, None


def _resolve_active_offer(merchant_id, recommendation_type):
    """Same shape as _resolve_target, but over active (already-approved)
    offers instead of pending ones — used by end_recommendation."""
    offers = db.get_active_offers(merchant_id)
    if not offers:
        return None, "There's nothing currently active to end."

    target = None
    if recommendation_type:
        target = next((o for o in offers if o["type"] == recommendation_type), None)

    if not target:
        if len(offers) == 1:
            target = offers[0]
        else:
            labels = ", ".join(f"{o['label']} ({o['type']})" for o in offers)
            return None, f"Ask the merchant which one they mean — currently active: {labels}."

    return target, None


def build_chat_tools(merchant, data, collected):
    """Same diagnostic tools as the scan, plus tools that actually DO
    something (approve/dismiss a pending recommendation, help with a
    device/account issue) — these are the tools that make chat genuinely
    agentic rather than a canned reply."""
    merchant_id = merchant["merchant_id"]
    catalog = get_effective_catalog(data, merchant_id)
    scan_tools = build_scan_tools(merchant, data, collected, catalog=catalog)

    @tool
    def approve_recommendation(recommendation_type: str = "") -> str:
        """Approve and execute a pending recommendation the merchant asked you to go ahead with — a growth promo, restock reorder, festival stock-up, slow-mover promo, loan offer, insurance offer, or feedback digest. Pass the type if you can tell which one they mean (promo, merchandising, reorder, festival_prep, loan, insurance, feedback_digest); leave blank if they just said something like 'approve it' and there's exactly one pending item."""
        target, error = _resolve_target(merchant_id, recommendation_type, "approve")
        if error:
            return error
        result = _execute_approve(target, data)
        collected["actioned_rec_id"] = target["id"]
        collected["action_result"] = result
        return _describe_approve_result(target, result)

    @tool
    def dismiss_recommendation(recommendation_type: str = "") -> str:
        """Dismiss a pending recommendation the merchant doesn't want. Same targeting rules as approve_recommendation (promo, merchandising, reorder, festival_prep, loan, insurance, feedback_digest)."""
        target, error = _resolve_target(merchant_id, recommendation_type, "dismiss")
        if error:
            return error
        result = dismiss_recommendation_action(merchant_id, target["id"], via="chat")
        collected["actioned_rec_id"] = target["id"]
        collected["action_result"] = result
        return f"Dismissed the {TYPE_LABEL.get(target['type'], target['type'])}."

    @tool
    def end_recommendation(recommendation_type: str = "") -> str:
        """End an offer that is already active — a promo already sent, a reorder already placed, an approved loan, or an insurance enrollment. This is for something ALREADY approved/running, not a pending card (use dismiss_recommendation for those). Pass the type if you can tell which one (promo, merchandising, reorder, festival_prep, loan, insurance); leave blank if there's exactly one active offer."""
        target, error = _resolve_active_offer(merchant_id, recommendation_type)
        if error:
            return error
        result = end_recommendation_action(merchant_id, target["id"], via="chat")
        collected["actioned_rec_id"] = target["id"]
        collected["action_result"] = result
        return f"Ended the {target['label']}."

    @tool
    def handle_device_support_issue(query: str) -> str:
        """Help the merchant with a problem with their OWN Paytm device or account — Soundbox volume/connectivity, QR code scanning, settlement/payout delays, or the merchant app itself. This is NOT for the merchant's customers — pass the merchant's message verbatim."""
        tracer = Tracer()
        result = run_support_agent(merchant, query, tracer)
        collected["trace"].extend(tracer.entries)
        return result["reply"]

    @tool
    def record_stock_sold(item_name: str, quantity: int) -> str:
        """Record that the merchant sold units of a catalog item that the system doesn't already know about — e.g. an in-person/offline sale. Decreases that item's current stock level immediately. Pass the item name as the merchant referred to it and the quantity sold."""
        tracer = Tracer()
        result = apply_stock_sold(merchant, catalog, item_name, quantity, tracer)
        collected["trace"].extend(tracer.entries)
        return result["message"]

    @tool
    def record_stock_restocked(item_name: str, quantity: int) -> str:
        """Record that the merchant received new stock / restocked a catalog item — increases that item's current stock level immediately. Pass the item name as the merchant referred to it and the quantity added."""
        tracer = Tracer()
        result = apply_stock_restocked(merchant, catalog, item_name, quantity, tracer)
        collected["trace"].extend(tracer.entries)
        return result["message"]

    return scan_tools + [
        approve_recommendation,
        dismiss_recommendation,
        end_recommendation,
        handle_device_support_issue,
        record_stock_sold,
        record_stock_restocked,
    ]
