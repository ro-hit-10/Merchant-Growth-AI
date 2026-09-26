from datetime import datetime, timezone

from app.guardrails.guardrails import (
    check_promo_guardrails,
    check_reorder_guardrails,
    check_loan_guardrails,
    check_insurance_guardrails,
    all_passed,
)
from app.store.db import db


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def send_campaign_action(merchant_id, template_id, customer_segment, discount_pct, item_id, rec_id, rec_type="promo"):
    checks = check_promo_guardrails(merchant_id, discount_pct, customer_segment)
    passed = all_passed(checks)

    audit_entry = db.add_audit_entry({
        "merchantId": merchant_id,
        "actionType": "campaign.send",
        "requestPayload": {"templateId": template_id, "customerSegment": customer_segment, "discountPct": discount_pct, "itemId": item_id},
        "guardrailChecks": checks,
        "outcome": "sent" if passed else "blocked",
        "reason": "Promo sent — all guardrails passed." if passed else f"Blocked: {'; '.join(c['rule'] for c in checks if not c['passed'])}",
    })

    if not passed:
        return {"status": "blocked", "guardrailChecks": checks, "auditEntry": audit_entry}

    db.add_campaign({"merchantId": merchant_id, "customerSegment": customer_segment, "itemId": item_id, "discountPct": discount_pct, "sentAt": _now_iso()})
    db.remove_recommendation(merchant_id, rec_id)
    db.add_active_offer(merchant_id, {
        "id": rec_id, "type": rec_type,
        "label": f"{discount_pct}% promo on {item_id}",
        "startedAt": _now_iso(),
    })

    return {"status": "sent", "channel": "WhatsApp Business API (mock)", "guardrailChecks": checks, "auditEntry": audit_entry}


def create_reorder_action(merchant_id, item_id, quantity, rec_id, trigger="low_stock"):
    checks = check_reorder_guardrails(merchant_id, item_id, quantity)
    passed = all_passed(checks)
    rec_type = "festival_prep" if trigger == "festival_prep" else "reorder"

    reason = (
        ("Proactive festival-readiness reorder created — all guardrails passed." if trigger == "festival_prep" else "Reorder request created — all guardrails passed.")
        if passed
        else f"Blocked: {'; '.join(c['rule'] for c in checks if not c['passed'])}"
    )
    audit_entry = db.add_audit_entry({
        "merchantId": merchant_id,
        "actionType": "inventory.reorder",
        "requestPayload": {"itemId": item_id, "quantity": quantity, "trigger": trigger},
        "guardrailChecks": checks,
        "outcome": "created" if passed else "blocked",
        "reason": reason,
    })

    if not passed:
        return {"status": "blocked", "guardrailChecks": checks, "auditEntry": audit_entry}

    db.add_reorder({"merchantId": merchant_id, "itemId": item_id, "quantity": quantity, "createdAt": _now_iso(), "trigger": trigger})
    db.remove_recommendation(merchant_id, rec_id)
    db.add_active_offer(merchant_id, {
        "id": rec_id, "type": rec_type,
        "label": f"reorder of {quantity} units of {item_id}",
        "startedAt": _now_iso(),
    })

    return {"status": "created", "guardrailChecks": checks, "auditEntry": audit_entry}


def apply_loan_action(merchant_id, tier, amount_inr, consent, lending_rules, merchant, rec_id):
    checks = check_loan_guardrails(merchant_id, consent)
    passed = all_passed(checks)

    if not passed:
        audit_entry = db.add_audit_entry({
            "merchantId": merchant_id,
            "actionType": "lending.apply",
            "requestPayload": {"tier": tier, "amountInr": amount_inr},
            "guardrailChecks": checks,
            "outcome": "blocked",
            "reason": f"Blocked: {'; '.join(c['rule'] for c in checks if not c['passed'])}",
        })
        return {"status": "blocked", "guardrailChecks": checks, "auditEntry": audit_entry}

    rules = lending_rules["working_capital_loan"]
    escalation_reasons = []
    if merchant["settlement_consistency_score"] < 0.8:
        escalation_reasons.append(f"settlement_consistency_score {merchant['settlement_consistency_score']} < 0.8")
    if amount_inr > rules["escalate_to_relationship_manager_above_inr"]:
        escalation_reasons.append(f"requested amount ₹{amount_inr} > ₹{rules['escalate_to_relationship_manager_above_inr']}")
    will_escalate = len(escalation_reasons) > 0

    db.add_loan_application({
        "merchantId": merchant_id, "tier": tier, "amountInr": amount_inr,
        "createdAt": _now_iso(), "status": "escalated" if will_escalate else "approved",
    })
    db.remove_recommendation(merchant_id, rec_id)

    if will_escalate:
        db.add_rm_queue_item({"merchantId": merchant_id, "tier": tier, "amountInr": amount_inr, "reasons": escalation_reasons, "queuedAt": _now_iso()})
        audit_entry = db.add_audit_entry({
            "merchantId": merchant_id,
            "actionType": "lending.apply",
            "requestPayload": {"tier": tier, "amountInr": amount_inr},
            "guardrailChecks": checks,
            "outcome": "escalated",
            "reason": f"Escalated to relationship manager: {'; '.join(escalation_reasons)}",
        })
        # Escalated applications sit with the relationship manager, not something
        # the merchant can "end" themselves — not registered as an active offer.
        return {"status": "escalated", "queuedForRelationshipManager": True, "reasons": escalation_reasons, "guardrailChecks": checks, "auditEntry": audit_entry}

    audit_entry = db.add_audit_entry({
        "merchantId": merchant_id,
        "actionType": "lending.apply",
        "requestPayload": {"tier": tier, "amountInr": amount_inr},
        "guardrailChecks": checks,
        "outcome": "approved",
        "reason": "Auto-approved — within escalation thresholds and all guardrails passed.",
    })
    db.add_active_offer(merchant_id, {"id": rec_id, "type": "loan", "label": f"{tier} tier loan", "startedAt": _now_iso()})
    return {"status": "approved", "guardrailChecks": checks, "auditEntry": audit_entry}


def apply_insurance_action(merchant_id, product_name, premium_inr, consent, rec_id):
    checks = check_insurance_guardrails(merchant_id, product_name, consent)
    passed = all_passed(checks)

    audit_entry = db.add_audit_entry({
        "merchantId": merchant_id,
        "actionType": "insurance.apply",
        "requestPayload": {"productName": product_name, "premiumInr": premium_inr},
        "guardrailChecks": checks,
        "outcome": "enrolled" if passed else "blocked",
        "reason": (f'Enrolled in "{product_name}" — all guardrails passed.' if passed else f"Blocked: {'; '.join(c['rule'] for c in checks if not c['passed'])}"),
    })

    if not passed:
        return {"status": "blocked", "guardrailChecks": checks, "auditEntry": audit_entry}

    db.add_insurance_enrollment({"merchantId": merchant_id, "productName": product_name, "premiumInr": premium_inr, "enrolledAt": _now_iso()})
    db.remove_recommendation(merchant_id, rec_id)
    db.add_active_offer(merchant_id, {"id": rec_id, "type": "insurance", "label": f"{product_name} insurance", "startedAt": _now_iso()})

    return {"status": "enrolled", "guardrailChecks": checks, "auditEntry": audit_entry}


def dismiss_recommendation_action(merchant_id, rec_id, via="ui"):
    db.remove_recommendation(merchant_id, rec_id)
    audit_entry = db.add_audit_entry({
        "merchantId": merchant_id,
        "actionType": "recommendation.dismiss",
        "requestPayload": {"recId": rec_id},
        "guardrailChecks": [],
        "outcome": "dismissed",
        "reason": "Merchant dismissed the recommendation via chat." if via == "chat" else "Merchant dismissed the recommendation card.",
    })
    return {"status": "dismissed", "auditEntry": audit_entry}


def end_recommendation_action(merchant_id, rec_id, via="ui"):
    offers = db.get_active_offers(merchant_id)
    target = next((o for o in offers if o["id"] == rec_id), None)
    if not target:
        return {"status": "not_found"}

    db.remove_active_offer(merchant_id, rec_id)
    audit_entry = db.add_audit_entry({
        "merchantId": merchant_id,
        "actionType": "recommendation.end",
        "requestPayload": {"recId": rec_id, "type": target["type"]},
        "guardrailChecks": [],
        "outcome": "ended",
        "reason": f"Merchant ended the active {target['label']}" + (" via chat." if via == "chat" else "."),
    })
    return {"status": "ended", "auditEntry": audit_entry, "type": target["type"], "label": target["label"]}
