from datetime import datetime, timezone

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from app.agents.agent_graph import run_agent, infer_routed_to, extract_text
from app.agents.tools import build_chat_tools
from app.store.db import db

SYSTEM_PROMPT_TEMPLATE = (
    "You are Vriddhi, an AI growth copilot embedded in the Paytm merchant app, talking directly to {name} "
    "({merchant_id}), a {category} merchant. Answer their message using the tools available to you — diagnose "
    "sales, stock, festival, loan, or insurance questions with the check_*/summarize_* tools, execute "
    "approve_recommendation or dismiss_recommendation when they ask you to act on something already recommended, "
    "and use handle_device_support_issue for problems with their OWN Soundbox/QR/app/settlement (not their "
    "customers). If they say something vague like 'approve it', call approve_recommendation with no type — if it "
    "tells you there are multiple pending items, ask the merchant which one they mean instead of guessing. If they "
    "ask to stop/end/cancel something ALREADY approved or sent (not a pending card), call end_recommendation "
    "instead of dismiss_recommendation. "
    "If the merchant tells you they sold units of something (e.g. an in-person sale not already in the system) or "
    "that they received/restocked units, call record_stock_sold or record_stock_restocked right away — don't just "
    "acknowledge it in words, the stock number has to actually change. Keep your final reply short, warm, and in "
    "plain language — no jargon, no markdown, 1-3 sentences."
)


def run_chat_agent(merchant_id, message, data):
    """The merchant-facing chat, now a real LangGraph agent: Gemini reads
    the message plus recent conversation history, decides which tool(s) to
    call (if any), executes them, and writes the reply — instead of a
    regex classifier guessing intent from keywords."""
    merchant = data.merchants_by_id[merchant_id]
    collected = {"trace": [], "recommendations": [], "actioned_rec_id": None, "action_result": None}
    tools = build_chat_tools(merchant, data, collected)

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(name=merchant["name"], merchant_id=merchant_id, category=merchant["category"])
    messages = [SystemMessage(content=system_prompt)]
    for h in db.get_chat_history(merchant_id)[-6:]:
        messages.append(HumanMessage(content=h["text"]) if h["role"] == "merchant" else AIMessage(content=h["text"]))
    messages.append(HumanMessage(content=message))

    final_message = run_agent(messages, tools, collected, recursion_limit=20)
    reply = extract_text(final_message.content)

    now = datetime.now(timezone.utc).isoformat()
    db.append_chat(merchant_id, {"role": "merchant", "text": message, "ts": now})
    db.append_chat(merchant_id, {"role": "vriddhi", "text": reply, "ts": now})

    return {
        "reply": reply,
        "routedTo": infer_routed_to(collected),
        "trace": collected["trace"],
        "actionedRecId": collected["actioned_rec_id"],
        "actionResult": collected["action_result"],
        "newRecommendations": collected["recommendations"],
    }
