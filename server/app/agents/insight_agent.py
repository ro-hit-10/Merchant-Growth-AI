from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.agents.llm import get_llm
from app.rag.knowledge_base import get_benchmark_store

WEEKEND_DAYS = {"Saturday", "Sunday"}

SYSTEM_PROMPT = """You are the Insight Agent inside Vriddhi, an AI growth copilot for Paytm merchants. Your job \
is to judge whether a merchant's computed weekday sales statistics represent a genuine, actionable anomaly — not \
normal week-to-week noise — and to explain it in plain language a small business owner would understand.

Rules:
- Ground every claim ONLY in the computed statistics and the retrieved category benchmark passage you are given. \
Never invent a benchmark number, percentage, or causal claim that isn't present in what you were given.
- A ratio below the category's expected floor is not automatically an anomaly — some merchants are naturally below \
their category average every week. Only call it a genuine anomaly if the computed data shows the merchant's OWN \
weekday revenue has dropped meaningfully versus their own recent baseline, sustained for at least 2 consecutive weeks.
- If it is an anomaly, cite the specific percentage drop and number of weeks in your narrative, and mention one or \
two of the retrieved passage's "common causes of weekday dip", if listed.
- If it is not an anomaly, say so plainly and briefly.
- Keep the narrative to 2-3 sentences, no markdown."""


class InsightDecision(BaseModel):
    has_anomaly: bool = Field(description="Whether the computed statistics represent a genuine, actionable anomaly per the retrieved benchmark policy")
    narrative: str = Field(description="2-3 sentence plain-language diagnosis for the merchant, grounded only in the given statistics and retrieved passage")


def _to_utc_days(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() // 86400)


def _weekday_weekend_avg(txns):
    weekday = [t for t in txns if t["day_of_week"] not in WEEKEND_DAYS]
    weekend = [t for t in txns if t["day_of_week"] in WEEKEND_DAYS]
    weekday_days = len({t["date"] for t in weekday}) or 1
    weekend_days = len({t["date"] for t in weekend}) or 1
    weekday_revenue = sum(t["amount_inr"] for t in weekday)
    weekend_revenue = sum(t["amount_inr"] for t in weekend)
    return weekday_revenue / weekday_days, weekend_revenue / weekend_days


def run_insight_agent(merchant, transactions, benchmark, data, tracer):
    """Deterministic math (weekly bucketing, ratios, baseline comparison)
    stays in Python — an LLM cannot reliably compute exact aggregates over
    thousands of transaction rows, and the original brief is explicit that
    no benchmark number should ever be invented. What's genuinely
    LLM-driven is the JUDGMENT CALL on top of those numbers (is this
    actually worth flagging?) and the explanation, grounded in a benchmark
    passage retrieved via embeddings — not a plain dict lookup."""

    tracer.log("insight_agent", f'Retrieving category benchmark for "{merchant["category"]}" from category_benchmarks.json')

    if not benchmark:
        tracer.log("insight_agent", f'No benchmark found for category "{merchant["category"]}" — skipping pattern diagnosis.')
        return {"has_anomaly": False}

    if not transactions:
        tracer.log("insight_agent", "No transaction history available — skipping pattern diagnosis.")
        return {"has_anomaly": False}

    as_of_day = max(_to_utc_days(t["date"]) for t in transactions)
    as_of_date = datetime.fromtimestamp(as_of_day * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
    tracer.log("insight_agent", f"Retrieved {len(transactions)} transactions from transactions.csv, most recent date {as_of_date}")

    weeks = [[], [], []]
    baseline = []
    for t in transactions:
        age = as_of_day - _to_utc_days(t["date"])
        if age < 7:
            weeks[0].append(t)
        elif age < 14:
            weeks[1].append(t)
        elif age < 21:
            weeks[2].append(t)
        else:
            baseline.append(t)

    expected_ratio = benchmark["expected_weekday_to_weekend_ratio"]
    anomaly_threshold = max(0, expected_ratio - 0.2)

    week_stats = []
    for idx, wtx in enumerate(weeks):
        weekday_avg, weekend_avg = _weekday_weekend_avg(wtx)
        ratio = (weekday_avg / weekend_avg) if weekend_avg > 0 else None
        week_stats.append({"week_index": idx, "weekday_avg_daily": weekday_avg, "weekend_avg_daily": weekend_avg, "ratio": ratio})
        tracer.log(
            "insight_agent",
            f"Week -{idx + 1}: weekday avg ₹{round(weekday_avg)}/day, weekend avg ₹{round(weekend_avg)}/day, "
            f"ratio {round(ratio, 2) if ratio is not None else 'n/a'} (expected ≥ {anomaly_threshold:.2f}, benchmark {expected_ratio})",
        )

    consecutive_flagged = 0
    for w in week_stats:
        if w["ratio"] is not None and w["ratio"] < anomaly_threshold:
            consecutive_flagged += 1
        else:
            break

    baseline_weekday_avg, baseline_weekend_avg = _weekday_weekend_avg(baseline or transactions)
    recent_weekday_avg = week_stats[0]["weekday_avg_daily"]
    pct_below_baseline = ((baseline_weekday_avg - recent_weekday_avg) / baseline_weekday_avg * 100) if baseline_weekday_avg > 0 else 0

    # --- RAG: retrieve the relevant benchmark passage via embeddings ------
    query = f"{merchant['category']} weekday sales dip causes and expected weekday to weekend ratio"
    hits = get_benchmark_store(data).similarity_search(query, k=1, filter_fn=lambda d: d["metadata"]["category"] == merchant["category"])
    if hits:
        retrieved_doc, score = hits[0]
        retrieved_text = retrieved_doc["text"]
        tracer.log("insight_agent", f'RAG retrieval: query="{query}" → matched "{retrieved_doc["id"]}" (cosine similarity {score:.3f})')
    else:
        retrieved_text = "No benchmark passage retrieved."
        tracer.log("insight_agent", "RAG retrieval returned no matches.")

    # --- LLM: judge + explain, grounded in the numbers above --------------
    stats_summary = (
        f"Merchant: {merchant['name']} ({merchant['category']}).\n"
        f"Week -1 (most recent): weekday avg ₹{round(week_stats[0]['weekday_avg_daily'])}/day, "
        f"weekend avg ₹{round(week_stats[0]['weekend_avg_daily'])}/day, ratio "
        f"{round(week_stats[0]['ratio'], 2) if week_stats[0]['ratio'] is not None else 'n/a'}.\n"
        f"Week -2: ratio {round(week_stats[1]['ratio'], 2) if week_stats[1]['ratio'] is not None else 'n/a'}.\n"
        f"Week -3: ratio {round(week_stats[2]['ratio'], 2) if week_stats[2]['ratio'] is not None else 'n/a'}.\n"
        f"Pre-window baseline weekday avg: ₹{round(baseline_weekday_avg)}/day.\n"
        f"Most recent weekday avg is {round(pct_below_baseline)}% {'below' if pct_below_baseline >= 0 else 'above'} that baseline.\n"
        f"Consecutive recent weeks with ratio below the category's anomaly floor ({anomaly_threshold:.2f}): {consecutive_flagged}.\n\n"
        f'Retrieved category benchmark passage:\n"{retrieved_text}"\n\n'
        "Decide whether this is a genuine, actionable anomaly and explain it to the merchant in plain language."
    )

    decision = get_llm().with_structured_output(InsightDecision).invoke(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": stats_summary}]
    )
    tracer.log("insight_agent", f"LLM diagnosis (has_anomaly={decision.has_anomaly}): {decision.narrative}")

    if not decision.has_anomaly:
        return {
            "has_anomaly": False,
            "week_stats": week_stats,
            "expected_ratio": expected_ratio,
            "anomaly_threshold": anomaly_threshold,
            "narrative": decision.narrative,
        }

    diagnosis = {
        "has_anomaly": True,
        "category": merchant["category"],
        "as_of_date": as_of_date,
        "expected_ratio": expected_ratio,
        "anomaly_threshold": anomaly_threshold,
        "consecutive_weeks_flagged": consecutive_flagged,
        "recent_weekday_avg": round(recent_weekday_avg),
        "recent_weekend_avg": round(week_stats[0]["weekend_avg_daily"]),
        "baseline_weekday_avg": round(baseline_weekday_avg),
        "pct_below_baseline": round(pct_below_baseline),
        "recent_ratio": round(week_stats[0]["ratio"], 2) if week_stats[0]["ratio"] is not None else None,
        "possible_causes": benchmark.get("common_causes_of_weekday_dip", []),
        "week_stats": week_stats,
        "citation": f"category_benchmarks.json:{merchant['category']}",
        "narrative": decision.narrative,
    }
    return diagnosis
