from pydantic import BaseModel, Field

from app.agents.llm import get_llm
from app.rag.knowledge_base import get_feedback_store

SYSTEM_PROMPT = """You are the Service Agent inside Vriddhi, an AI growth copilot for Paytm merchants. You are \
given a merchant's recent customer feedback (retrieved via semantic search, not a keyword list) plus the exact \
average rating (already computed, never yours to recalculate). Summarize the feedback into a short, plain-language \
digest: the average rating, the 1-2 themes that actually show up in the retrieved reviews, and whether anything \
needs attention. Ground every claim in the reviews given — never invent a theme that isn't actually present."""


class FeedbackDigest(BaseModel):
    digest: str = Field(description="2-3 sentence plain-language digest: avg rating, real themes from the given reviews, anything needing attention")


def run_service_agent(merchant, feedback, data, tracer):
    """Summarizes recent customer_feedback.csv rows into a short digest.
    Retrieval (which reviews are thematically similar to each other) is via
    embeddings, not a hand-maintained keyword dictionary; the average
    rating is computed exactly, and the LLM's job is synthesizing themes
    from the actual review text."""
    tracer.log("service_agent", f"Summarizing {len(feedback)} feedback rows from customer_feedback.csv.")

    if not feedback:
        tracer.log("service_agent", "No feedback recorded for this merchant yet.")
        return None

    avg_rating = sum(f["rating"] for f in feedback) / len(feedback)
    negative_count = sum(1 for f in feedback if f["rating"] <= 2)

    store = get_feedback_store(merchant["merchant_id"], feedback)
    hits = store.similarity_search("recurring themes, complaints, and praise in customer feedback", k=min(6, len(feedback)))
    for d, score in hits:
        tracer.log("service_agent", f'RAG retrieval: matched review "{d["id"]}" (cosine similarity {score:.3f})')
    retrieved_reviews = "\n".join(f"- {d['text']}" for d, _ in hits)

    prompt = (
        f"Average rating: {avg_rating:.1f}/5 across {len(feedback)} reviews. {negative_count} rated 2★ or below.\n\n"
        f"Retrieved reviews:\n{retrieved_reviews}"
    )
    draft = get_llm().with_structured_output(FeedbackDigest).invoke(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    )
    tracer.log("service_agent", f"Digest: {draft.digest}")

    return {
        "id": f"REC-FEEDBACK-{merchant['merchant_id']}",
        "type": "feedback_digest",
        "merchantId": merchant["merchant_id"],
        "avgRating": round(avg_rating, 1),
        "reviewCount": len(feedback),
        "message": draft.digest,
        "reasoning": "Weekly summary of customer_feedback.csv for this merchant, retrieved and synthesized via semantic search over the actual reviews.",
    }


def match_auto_reply(query, merchant, templates):
    """Answers the MERCHANT'S OWN CUSTOMERS (store hours, returns) — a
    different surface from the merchant-facing agents above, which answer
    the merchant's own questions about their business or device/account.
    Kept as simple template matching since it's a fixed, small set of
    common storefront questions, not something requiring reasoning."""
    q = query.lower()
    svc = templates["customer_service_autoreply"]["templates"]

    if any(k in q for k in ("open", "hours", "timing", "close")):
        return {
            "matched": "store_hours",
            "reply": svc["store_hours"]
            .replace("{merchant_name}", merchant["name"])
            .replace("{open_time}", "10:00 AM")
            .replace("{close_time}", "8:00 PM")
            .replace("{days_open}", "Monday–Sunday"),
        }
    if any(k in q for k in ("return", "refund", "exchange")):
        return {
            "matched": "return_policy",
            "reply": svc["return_policy"].replace("{merchant_name}", merchant["name"]).replace("{return_days}", "7"),
        }
    if any(k in q for k in ("order", "status", "track")):
        return {
            "matched": "order_status",
            "reply": svc["order_status"].replace("{order_id}", "N/A").replace("{status}", "being prepared").replace("{eta}", "today"),
        }
    return {"matched": None, "reply": f"Thanks for reaching out to {merchant['name']}! A team member will get back to you shortly."}
