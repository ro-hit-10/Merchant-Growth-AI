import json
from pathlib import Path

from app.rag.embeddings import get_embeddings
from app.rag.vector_store import VectorStore

PRECOMPUTED_DIR = Path(__file__).resolve().parent / "precomputed"

# Lazily built, process-lifetime caches — building requires the embeddings
# API (and therefore the API key), so we build on first use rather than at
# server startup, keeping the health/merchants endpoints usable even before
# a key is configured.
_cache = {}
_feedback_cache = {}


def _build(key, doc_builder):
    """Static corpora (everything except per-merchant feedback) never
    change at runtime, so prefer a precomputed vector file
    (scripts/precompute_embeddings.py) over a live embeddings call —
    this matters because Render's free tier spins the process down after
    ~15 min idle, and the process-lifetime _cache above resets to empty on
    every cold start. Without this, the FIRST request after any idle gap
    pays for re-embedding every static knowledge base from scratch, on
    top of an already-tight free-tier rate limit. Falls back to a live
    call if the precomputed file is missing (e.g. the dataset changed and
    the script hasn't been re-run yet) so nothing breaks, just runs slower
    on that one cold request."""
    if key not in _cache:
        precomputed_path = PRECOMPUTED_DIR / f"{key}.json"
        if precomputed_path.exists():
            payload = json.loads(precomputed_path.read_text(encoding="utf-8"))
            _cache[key] = VectorStore.from_precomputed(get_embeddings(), payload["docs"], payload["vectors"])
        else:
            store = VectorStore(get_embeddings())
            store.add_documents(doc_builder(None))
            _cache[key] = store
    return _cache[key]


def _build_benchmark_docs(data):
    """One document per category from category_benchmarks.json — the
    Insight Agent's RAG corpus."""
    docs = []
    for category, b in data.category_benchmarks.items():
        text = (
            f"Category: {category}. Expected weekday-to-weekend revenue ratio: {b['expected_weekday_to_weekend_ratio']}. "
            f"{b['note']} Typical average ticket size: ₹{b['typical_avg_ticket_inr']}. "
            f"Seasonal peaks: {', '.join(b['seasonal_peaks'])}. "
            f"Common causes of a weekday dip: {', '.join(b['common_causes_of_weekday_dip'])}."
        )
        docs.append({"id": category, "text": text, "metadata": {"category": category}})
    return docs


def get_benchmark_store(data):
    return _build("benchmarks", lambda _: _build_benchmark_docs(data))


def _build_seasonal_peaks_docs(data):
    """One document per (category, individual seasonal peak) pair — lets
    the Festival Agent semantically match a merchant's free-text festival
    mention ("diwali", "wedding season") against the dataset's phrasing,
    across all categories, not just an exact substring match."""
    docs = []
    for category, b in data.category_benchmarks.items():
        for peak in b.get("seasonal_peaks", []):
            docs.append({
                "id": f"{category}:{peak}",
                "text": f"{category} seasonal peak: {peak}",
                "metadata": {"category": category, "peak_text": peak},
            })
    return docs


def get_seasonal_peaks_store(data):
    return _build("seasonal_peaks", lambda _: _build_seasonal_peaks_docs(data))


def _build_lending_docs(data):
    """Working-capital loan tiers/thresholds, escalation rules, and
    insurance products from lending_eligibility_rules.json — the Financial
    Agent's RAG corpus."""
    wc = data.lending_rules["working_capital_loan"]
    docs = [{
        "id": "wc_eligibility",
        "text": (
            f"Working capital loan eligibility criteria: minimum {wc['eligibility_criteria']['min_months_on_platform']} "
            f"months on platform, minimum avg monthly settlement ₹{wc['eligibility_criteria']['min_avg_monthly_settlement_inr']}, "
            f"minimum settlement consistency score {wc['eligibility_criteria']['min_settlement_consistency_score']}."
        ),
        "metadata": {"type": "loan_eligibility"},
    }]
    for tier in wc["tiers"]:
        docs.append({
            "id": f"tier_{tier['tier']}",
            "text": (
                f"{tier['tier']} tier working capital loan: minimum avg monthly settlement "
                f"₹{tier['min_avg_monthly_settlement_inr']}, max loan ₹{tier['max_loan_inr']}, interest rate "
                f"{tier['indicative_interest_rate_pct']}%, tenure {tier['tenure_months']} months."
            ),
            "metadata": {"type": "loan_tier", "tier": tier["tier"]},
        })
    docs.append({
        "id": "wc_thresholds",
        "text": (
            f"Auto-approve threshold ₹{wc['auto_approve_threshold_inr']}. Escalate to relationship manager if "
            f"requested amount exceeds ₹{wc['escalate_to_relationship_manager_above_inr']}."
        ),
        "metadata": {"type": "loan_thresholds"},
    })
    for i, rule in enumerate(data.lending_rules["escalation_rules"]):
        docs.append({"id": f"escalation_{i}", "text": f"Escalation rule: {rule}", "metadata": {"type": "escalation_rule"}})
    for p in data.lending_rules["merchant_insurance"]["products"]:
        docs.append({
            "id": f"insurance_{p['name']}",
            "text": f"{p['name']}: eligibility — {p['eligibility']}. Indicative annual premium ₹{p['indicative_annual_premium_inr']}.",
            "metadata": {"type": "insurance_product", "name": p["name"]},
        })
    return docs


def get_lending_store(data):
    return _build("lending", lambda _: _build_lending_docs(data))


def _build_campaign_template_docs(data):
    """One document per merchant-facing message template from
    campaign_templates.json, guardrails included — grounds the Growth
    Action / Ops agents' drafted message tone and constraints."""
    docs = []
    for key, tmpl in data.campaign_templates.items():
        if key == "customer_service_autoreply":
            continue  # a different surface (merchant's own customers), not used by these agents
        guardrails_text = "; ".join(tmpl.get("guardrails", [])) or "none specified"
        docs.append({
            "id": key,
            "text": f'{key} template (channel: {tmpl["channel"]}, trigger: {tmpl["trigger"]}): "{tmpl["template"]}". Guardrails: {guardrails_text}.',
            "metadata": {"template_id": key},
        })
    return docs


def get_campaign_template_store(data):
    return _build("campaign_templates", lambda _: _build_campaign_template_docs(data))


SUPPORT_ARTICLES = [
    {
        "id": "soundbox_volume",
        "text": (
            "Soundbox volume is too low or quiet. Fix: press the volume button on the side of the Soundbox to "
            "increase it — it ships at a low default level. Make sure it isn't muffled under the counter or wrapped "
            "in anything. A low battery reduces speaker output — put it on charge for 15 minutes and try again. If "
            "still too quiet, hold the power button for 10 seconds to restart the device."
        ),
    },
    {
        "id": "soundbox_offline",
        "text": (
            "Soundbox is offline or won't connect to the network. Fix: check the SIM/network indicator light on the "
            "device — a red light means no network. Move the device closer to a window or open area if signal is "
            "weak indoors. Restart the device by holding the power button for 10 seconds."
        ),
    },
    {
        "id": "qr_scan",
        "text": (
            "QR code isn't scanning, is faded, or is damaged. Fix: wipe the QR sticker clean — smudges or glare are "
            "the most common cause of failed scans. Make sure there's enough light and the customer's phone camera "
            "has scan permission enabled. If the sticker is physically faded or torn, a replacement QR request can "
            "be raised."
        ),
    },
    {
        "id": "settlement_delay",
        "text": (
            "Settlement or payout is delayed, or a payment hasn't been credited. Fix: settlements are usually "
            "credited by the next working day (T+1). Delays beyond 2 business days are almost always a bank-side "
            "processing issue or a KYC/bank-detail mismatch — double-check linked bank account details. If it's been "
            "more than 2 business days, this can be escalated to a settlement specialist."
        ),
    },
    {
        "id": "app_issue",
        "text": (
            "The merchant app is crashing, won't open, has a login error, or needs an update. Fix: force-close and "
            "reopen the Paytm for Business app. Check the Play Store / App Store for a pending update. Confirm a "
            "stable internet connection. If it's still broken after a restart, a support ticket can be raised."
        ),
    },
]


def _build_support_kb_docs(_data=None):
    return SUPPORT_ARTICLES


def get_support_kb_store():
    return _build("support_kb", _build_support_kb_docs)


# Every static corpus, keyed by its precomputed-file name — used both by
# `_build` above (as the live-embedding fallback) and by
# scripts/precompute_embeddings.py (to generate the precomputed files from
# the exact same doc-building logic, so the two never drift apart).
STATIC_DOC_BUILDERS = {
    "benchmarks": _build_benchmark_docs,
    "seasonal_peaks": _build_seasonal_peaks_docs,
    "lending": _build_lending_docs,
    "campaign_templates": _build_campaign_template_docs,
    "support_kb": _build_support_kb_docs,
}


def get_feedback_store(merchant_id, feedback_rows):
    """Built per merchant on demand (feedback differs per merchant, so this
    one can't be precomputed the same way) — cached by merchant + row count
    so a fresh scan doesn't re-embed the same handful of reviews every
    time within one process's lifetime."""
    key = f"{merchant_id}:{len(feedback_rows)}"
    if key not in _feedback_cache:
        store = VectorStore(get_embeddings())
        docs = [
            {"id": f"{merchant_id}-{i}", "text": f"Rating {f['rating']}/5: {f['comment']}", "metadata": {"rating": f["rating"], "date": f["date"]}}
            for i, f in enumerate(feedback_rows)
        ]
        store.add_documents(docs)
        _feedback_cache[key] = store
    return _feedback_cache[key]
