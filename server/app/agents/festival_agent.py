import math
import re
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from app.agents.llm import get_llm
from app.rag.knowledge_base import get_seasonal_peaks_store

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

SYSTEM_PROMPT = """You are the Festival Readiness Agent inside Vriddhi, an AI growth copilot for Paytm merchants. \
You are given exact, already-computed stock-uplift math (never yours to recalculate) for a seasonal peak and one \
catalog item. Write two things: a short heads-up message to show the merchant on a recommendation card, and a \
1-2 sentence reasoning explaining the estimate is a planning heuristic, not a cited benchmark figure. Use the exact \
numbers given — never invent or round differently."""


class FestivalNarrative(BaseModel):
    message: str = Field(description="Short heads-up message for a recommendation card, using the exact festival label, timing, extra quantity, and item name given")
    reasoning: str = Field(description="1-2 sentence explanation for the merchant, noting the uplift estimate is a planning heuristic")


def _month_idx(name):
    return MONTH_MAP.get(name[:3].lower())


def _parse_peak(raw):
    """category_benchmarks.json seasonal_peaks are free text like
    "Diwali (Oct-Nov)" or "Wedding season (Nov-Feb)". Parse an explicit
    "(Month-Month)" range where present, and special-case "Diwali"
    (mentioned without a bracketed range in some categories) to its
    well-known Oct-Nov window. Peaks we can't confidently place on a
    calendar (e.g. "Onam/Pongal regional peaks") are left unparsed —
    calendar placement is deterministic math, not something to guess at
    via embeddings or an LLM."""
    match = re.search(r"\(([A-Za-z]{3,9})\s*-\s*([A-Za-z]{3,9})\)", raw)
    if match:
        start_month = _month_idx(match.group(1))
        end_month = _month_idx(match.group(2))
        if start_month and end_month:
            label = re.sub(r"\s*\([^)]*\)", "", raw).strip()
            return {"label": label, "start_month": start_month, "end_month": end_month}
    if "diwali" in raw.lower():
        label = re.sub(r"\s*\([^)]*\)", "", raw).strip() or "Diwali"
        return {"label": label, "start_month": 10, "end_month": 11}
    return None


def _evaluate_window(as_of: datetime, start_month, end_month):
    wraps = end_month < start_month

    def occurrence(year):
        start = datetime(year, start_month, 1, tzinfo=timezone.utc)
        if wraps:
            end_year, end_m = (year + 1, end_month + 1) if end_month < 12 else (year + 2, 1)
        else:
            end_year, end_m = (year, end_month + 1) if end_month < 12 else (year + 1, 1)
        end = datetime(end_year, end_m, 1, tzinfo=timezone.utc) - timedelta(days=1)
        return start, end

    start, end = occurrence(as_of.year)
    if as_of > end:
        start, end = occurrence(as_of.year + 1)

    if start <= as_of <= end:
        return {"status": "ongoing", "days_until_start": 0}
    return {"status": "upcoming", "days_until_start": round((start - as_of).total_seconds() / 86400)}


LOOKAHEAD_DAYS = 60
UPLIFT_MULTIPLIER = 1.6
TOP_N_ITEMS = 2


def _resolve_festival_hint(merchant, festival_hint, data, tracer):
    """Semantic match via embeddings — not a substring/exact-text
    comparison — so a merchant's free-text mention ("diwali", "wedding
    season") is matched against the dataset's own phrasing even if worded
    differently. The winning document's raw peak text is re-parsed for its
    calendar window (that placement is deterministic math, not something
    retrieval should guess at)."""
    hits = get_seasonal_peaks_store(data).similarity_search(
        f"{merchant['category']} {festival_hint}", k=1,
        filter_fn=lambda d: d["metadata"]["category"] == merchant["category"],
    )
    if not hits:
        return None
    retrieved_doc, score = hits[0]
    tracer.log("ops_agent", f'RAG retrieval: query="{festival_hint}" (category {merchant["category"]}) → matched "{retrieved_doc["id"]}" (cosine similarity {score:.3f})')
    if score < 0.5:
        return None
    return _parse_peak(retrieved_doc["metadata"]["peak_text"])


def plan_festival_readiness(merchant, catalog, benchmark, transactions, data, tracer, festival_hint=None):
    """Proactively reasons about whether a merchant should stock up ahead
    of a seasonal peak — independent of the normal reorder_threshold check,
    since "stock looks fine for a normal week" and "stock is enough for a
    festival rush" are different questions. Calendar placement and uplift
    quantity are deterministic math; matching a merchant's free-text
    festival mention is retrieval (embeddings); the card's copy is an LLM
    call grounded in both."""
    if not benchmark or not benchmark.get("seasonal_peaks"):
        return None

    if transactions:
        as_of = max(datetime.strptime(t["date"], "%Y-%m-%d") for t in transactions).replace(tzinfo=timezone.utc)
    else:
        as_of = datetime.now(timezone.utc)

    parsed_peaks = [p for p in (_parse_peak(raw) for raw in benchmark["seasonal_peaks"]) if p]
    if not parsed_peaks:
        return None

    candidates = [{**p, "window": _evaluate_window(as_of, p["start_month"], p["end_month"])} for p in parsed_peaks]

    if festival_hint:
        hinted = _resolve_festival_hint(merchant, festival_hint, data, tracer)
        if not hinted:
            return {"no_match": True}
        candidates = [{**hinted, "window": _evaluate_window(as_of, hinted["start_month"], hinted["end_month"])}]
    else:
        candidates = [c for c in candidates if c["window"]["status"] == "ongoing" or c["window"]["days_until_start"] <= LOOKAHEAD_DAYS]
        if not candidates:
            return None

    candidates.sort(key=lambda c: c["window"]["days_until_start"])
    peak = candidates[0]

    timing_phrase = "currently active" if peak["window"]["status"] == "ongoing" else f"{peak['window']['days_until_start']} days away"
    tracer.log(
        "ops_agent",
        f"Seasonal check: \"{peak['label']}\" is {timing_phrase} for {merchant['category']}. Evaluating whether "
        "current stock covers the expected demand uplift, independent of the normal reorder_threshold.",
    )

    top_items = sorted([c for c in catalog if c["reorder_threshold"] > 0], key=lambda c: -c["units_sold_last_30d"])[:TOP_N_ITEMS]

    plan = []
    for item in top_items:
        avg_daily_units = item["units_sold_last_30d"] / 30
        uplift_target = math.ceil(avg_daily_units * 30 * UPLIFT_MULTIPLIER)
        recommended_extra_qty = max(0, uplift_target - item["stock_qty"])
        if recommended_extra_qty > 0:
            plan.append({"item": item, "avg_daily_units": avg_daily_units, "uplift_target": uplift_target, "recommended_extra_qty": recommended_extra_qty})

    plan.sort(key=lambda p: -p["recommended_extra_qty"])

    if not plan:
        tracer.log(
            "ops_agent",
            f"Even at an estimated ~{round((UPLIFT_MULTIPLIER - 1) * 100)}% festival demand uplift (a planning heuristic, "
            "not a cited benchmark figure), current stock on top sellers already covers it — no extra stock-up needed yet.",
        )
        return {"has_recommendation": False, "peak": peak}

    best = plan[0]
    item, avg_daily_units, uplift_target, recommended_extra_qty = best["item"], best["avg_daily_units"], best["uplift_target"], best["recommended_extra_qty"]

    prompt = (
        f"Festival/peak: {peak['label']} ({timing_phrase}).\n"
        f"Merchant category: {merchant['category']}.\n"
        f"Item: {item['item_name']}. Normal pace: ~{avg_daily_units:.1f} units/day. Estimated uplift target for the "
        f"period: {uplift_target} units. Current stock: {item['stock_qty']}. Recommended extra units to bring in: "
        f"{recommended_extra_qty}."
    )
    narrative = get_llm().with_structured_output(FestivalNarrative).invoke(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    )

    tracer.log(
        "ops_agent",
        f"{item['item_name']}: recommending {recommended_extra_qty} extra units ahead of \"{peak['label']}\" "
        f"(uplift target {uplift_target} vs. {item['stock_qty']} in stock).",
        {"itemId": item["item_id"], "recommendedExtraQty": recommended_extra_qty},
    )

    return {
        "has_recommendation": True,
        "peak": peak,
        "recommendation": {
            "id": f"REC-FESTIVAL-{merchant['merchant_id']}-{item['item_id']}",
            "type": "festival_prep",
            "merchantId": merchant["merchant_id"],
            "itemId": item["item_id"],
            "itemName": item["item_name"],
            "stockQty": item["stock_qty"],
            "recommendedQty": recommended_extra_qty,
            "festivalLabel": peak["label"],
            "windowStatus": peak["window"]["status"],
            "daysUntilStart": peak["window"]["days_until_start"],
            "message": narrative.message,
            "reasoning": narrative.reasoning,
        },
    }
