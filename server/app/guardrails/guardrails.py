from datetime import datetime, timezone

from app.config import GUARDRAILS
from app.store.db import db


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _days_between(a: str, b: datetime) -> float:
    return abs((b - _parse(a)).total_seconds()) / 86400


def check_promo_guardrails(merchant_id, discount_pct, customer_segment, max_discount_pct=None):
    checks = []
    cap = max_discount_pct if max_discount_pct is not None else GUARDRAILS["default_max_discount_pct"]

    checks.append({
        "rule": f"discount_pct must not exceed max_discount_pct ({cap}%)",
        "passed": discount_pct <= cap,
        "detail": f"requested {discount_pct}%, cap {cap}%",
    })

    history = [c for c in db.get_campaign_history(merchant_id) if c["customerSegment"] == customer_segment]
    now = datetime.now(timezone.utc)
    recent = next((c for c in history if _days_between(c["sentAt"], now) < GUARDRAILS["promo_repeat_window_days"]), None)
    checks.append({
        "rule": f"max 1 promo per customer segment per {GUARDRAILS['promo_repeat_window_days']} days",
        "passed": recent is None,
        "detail": (
            f'segment "{customer_segment}" already received a promo on {recent["sentAt"]}'
            if recent
            else f'no promo sent to segment "{customer_segment}" in the last {GUARDRAILS["promo_repeat_window_days"]} days'
        ),
    })

    return checks


def check_reorder_guardrails(merchant_id, item_id, quantity):
    checks = [{
        "rule": "reorder quantity must be positive",
        "passed": quantity > 0,
        "detail": f"requested quantity {quantity}",
    }]

    history = [r for r in db.get_reorders(merchant_id) if r["itemId"] == item_id]
    now = datetime.now(timezone.utc)
    recent = next((r for r in history if _days_between(r["createdAt"], now) < GUARDRAILS["reorder_repeat_window_days"]), None)
    checks.append({
        "rule": f"no duplicate reorder for the same item within {GUARDRAILS['reorder_repeat_window_days']} days",
        "passed": recent is None,
        "detail": (
            f'item {item_id} already had a reorder request on {recent["createdAt"]}'
            if recent
            else f"no recent reorder request for item {item_id}"
        ),
    })

    return checks


def check_loan_guardrails(merchant_id, consent):
    checks = [{
        "rule": "no auto-submission without explicit one-tap merchant consent",
        "passed": consent is True,
        "detail": "merchant provided explicit consent" if consent is True else "consent flag missing or false",
    }]

    history = db.get_loan_applications(merchant_id)
    now = datetime.now(timezone.utc)
    recent = next((l for l in history if _days_between(l["createdAt"], now) < GUARDRAILS["loan_nudge_repeat_window_days"]), None)
    checks.append({
        "rule": f"loan nudge shown/applied at most once per {GUARDRAILS['loan_nudge_repeat_window_days']} days",
        "passed": recent is None,
        "detail": (
            f'a loan application already exists from {recent["createdAt"]}'
            if recent
            else f"no loan application in the last {GUARDRAILS['loan_nudge_repeat_window_days']} days"
        ),
    })

    return checks


def check_insurance_guardrails(merchant_id, product_name, consent):
    checks = [{
        "rule": "no auto-submission without explicit one-tap merchant consent",
        "passed": consent is True,
        "detail": "merchant provided explicit consent" if consent is True else "consent flag missing or false",
    }]

    existing = next((e for e in db.get_insurance_enrollments(merchant_id) if e["productName"] == product_name), None)
    checks.append({
        "rule": "no duplicate enrollment in the same insurance product",
        "passed": existing is None,
        "detail": (
            f'already enrolled in "{product_name}" on {existing["enrolledAt"]}'
            if existing
            else f'not currently enrolled in "{product_name}"'
        ),
    })

    return checks


def all_passed(checks):
    return all(c["passed"] for c in checks)
