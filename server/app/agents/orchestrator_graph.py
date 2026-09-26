from langchain_core.messages import SystemMessage, HumanMessage

from app.agents.agent_graph import run_agent, extract_text
from app.agents.tools import build_scan_tools


def run_orchestrator_scan(merchant, data):
    """The orchestrator's scheduled diagnostic scan, now genuinely
    LLM-driven: Gemini reads the merchant's context, decides which
    diagnostic tools to call and in what order, and stops once it judges
    the picture complete. It never invents a number — every tool call
    grounds its answer in the actual dataset; the LLM only decides the
    *plan* (which tools, in what order) and writes the final summary."""
    collected = {"trace": [], "recommendations": [], "diagnosis": None}
    tools = build_scan_tools(merchant, data, collected)

    system_prompt = (
        f"You are Vriddhi, an AI growth copilot for Paytm merchants, running a scheduled diagnostic scan for "
        f"{merchant['name']} ({merchant['merchant_id']}), category: {merchant['category']}.\n\n"
        "Call tools to build a complete picture of this merchant's business: sales pattern health, slow-moving "
        "catalog items, stock levels, upcoming seasonal/festival readiness, working-capital loan eligibility, "
        "insurance eligibility, and recent customer feedback. Call check_sales_pattern before "
        "draft_dip_recovery_promo — that tool needs a confirmed anomaly first and will refuse otherwise. Call each "
        "relevant tool at most once. Once you've covered all of these areas, respond with a brief 2-3 sentence "
        "summary of what you found, written for a small business owner — no jargon."
    )
    messages = [SystemMessage(content=system_prompt), HumanMessage(content="Run the full diagnostic scan now.")]

    final_message = run_agent(messages, tools, collected)

    return {
        "trace": collected["trace"],
        "recommendations": collected["recommendations"],
        "diagnosis": collected["diagnosis"],
        "summary": extract_text(final_message.content),
    }
