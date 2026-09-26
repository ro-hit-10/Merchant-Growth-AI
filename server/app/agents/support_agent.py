import time

from pydantic import BaseModel, Field

from app.agents.llm import get_llm
from app.rag.knowledge_base import get_support_kb_store
from app.store.db import db

SYSTEM_PROMPT = """You are the Support Agent inside Vriddhi, an AI growth copilot for Paytm merchants. You handle \
the merchant's OWN device/account problems (Soundbox, QR code, settlement, the merchant app) — NOT the merchant's \
customers. You are given the merchant's message and knowledge-base article(s) retrieved via semantic search.

Decide whether the merchant is asking you to escalate (they say a fix didn't work, ask for a technician, or \
explicitly say "raise a ticket"/"escalate"), or whether this is a fresh troubleshooting request.
- If fresh: reply with the troubleshooting steps from the retrieved article(s), in your own words but grounded \
only in what was retrieved — never invent a fix not present in the article. If nothing relevant was retrieved, \
ask a clarifying question instead of guessing.
- If escalating: write a short, warm acknowledgement (a ticket number will be appended separately, don't invent one).
Keep replies to 2-4 sentences, no markdown."""


class SupportResponse(BaseModel):
    should_raise_ticket: bool = Field(description="True only if the merchant is asking to escalate, says a previous fix didn't work, or explicitly asks for a technician/ticket")
    reply: str = Field(description="The reply to send the merchant")


def run_support_agent(merchant, query, tracer):
    """Handles the merchant's OWN device/account support questions —
    mirroring what the existing Paytm merchant assistant already does
    today. Retrieval is semantic search over an authored troubleshooting
    knowledge base (app/rag/knowledge_base.py), not a static regex list;
    the LLM decides fresh-troubleshooting vs. escalate and writes the
    reply grounded in whatever was actually retrieved."""
    hits = get_support_kb_store().similarity_search(query, k=2)
    for d, score in hits:
        tracer.log("support_agent", f'RAG retrieval: matched KB article "{d["id"]}" (cosine similarity {score:.3f})')
    retrieved = "\n\n".join(d["text"] for d, _ in hits) if hits else "No relevant article retrieved."

    prompt = f'Merchant message: "{query}"\n\nRetrieved knowledge base article(s):\n{retrieved}'
    decision = get_llm().with_structured_output(SupportResponse).invoke(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    )

    if decision.should_raise_ticket:
        ticket = {
            "id": f"TCK-{int(time.time() * 1000)}",
            "merchantId": merchant["merchant_id"],
            "description": query,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "open",
        }
        db.add_support_ticket(ticket)
        db.add_audit_entry({
            "merchantId": merchant["merchant_id"],
            "actionType": "support.ticket_created",
            "requestPayload": {"description": query},
            "guardrailChecks": [],
            "outcome": "created",
            "reason": f"Support ticket {ticket['id']} raised for merchant-reported issue.",
        })
        tracer.log("support_agent", f"LLM decided to escalate — raised support ticket {ticket['id']} for merchant {merchant['merchant_id']}.")
        return {"reply": f"{decision.reply} (Ticket {ticket['id']} raised — our team will reach out within 24 hours.)"}

    tracer.log("support_agent", f"LLM replied with troubleshooting grounded in {len(hits)} retrieved article(s).")
    return {"reply": decision.reply}
