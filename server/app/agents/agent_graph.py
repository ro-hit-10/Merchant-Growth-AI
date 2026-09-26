from typing import Annotated, Any, TypedDict

from langchain_core.messages import ToolMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages

from app.agents.llm import get_llm
from app.agents.tracer import Tracer

TOOL_TO_AGENT = {
    "check_sales_pattern": "insight_agent",
    "draft_dip_recovery_promo": "growth_action_agent",
    "check_slow_movers": "growth_action_agent",
    "check_stock_levels": "ops_agent",
    "check_festival_readiness": "ops_agent",
    "check_loan_eligibility": "financial_agent",
    "check_insurance_eligibility": "financial_agent",
    "summarize_feedback": "service_agent",
    "approve_recommendation": "orchestrator",
    "dismiss_recommendation": "orchestrator",
    "handle_device_support_issue": "support_agent",
}


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    tool_map: dict
    collected: Any


def _llm_node(state: AgentState):
    llm_with_tools = get_llm().bind_tools(list(state["tool_map"].values()))
    tracer = Tracer()
    response = llm_with_tools.invoke(state["messages"])
    tool_calls = getattr(response, "tool_calls", None)
    if tool_calls:
        names = ", ".join(tc["name"] for tc in tool_calls)
        tracer.log("orchestrator", f"LLM decided to call: {names}")
    else:
        tracer.log("orchestrator", f"LLM final response: {extract_text(response.content)}")
    state["collected"]["trace"].extend(tracer.entries)
    return {"messages": [response]}


def _tools_node(state: AgentState):
    last = state["messages"][-1]
    tracer = Tracer()
    outputs = []
    for tc in last.tool_calls:
        tool_fn = state["tool_map"][tc["name"]]
        try:
            result = tool_fn.invoke(tc["args"])
        except Exception as e:  # noqa: BLE001 — surface the failure to the LLM instead of crashing the graph
            result = f"Tool error: {e}"
        state["collected"].setdefault("tools_called", []).append(tc["name"])
        tracer.log(TOOL_TO_AGENT.get(tc["name"], "orchestrator"), f"Tool `{tc['name']}` result: {str(result)[:300]}")
        outputs.append(ToolMessage(content=str(result), tool_call_id=tc["id"], name=tc["name"]))
    state["collected"]["trace"].extend(tracer.entries)
    return {"messages": outputs}


def _should_continue(state: AgentState):
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else END


def _build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("llm", _llm_node)
    graph.add_node("tools", _tools_node)
    graph.add_edge(START, "llm")
    graph.add_conditional_edges("llm", _should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "llm")
    return graph.compile()


# The graph's *structure* (LLM decides -> execute tools -> LLM decides again,
# until it stops calling tools) never changes — only the tool_map bound into
# each invocation's state differs between a scheduled scan and a chat
# message. Compiling once and reusing it is safe and avoids rebuilding the
# graph on every request.
_compiled_agent_graph = _build_graph()


def run_agent(messages, tools, collected, recursion_limit=30):
    tool_map = {t.name: t for t in tools}
    initial_state: AgentState = {"messages": messages, "tool_map": tool_map, "collected": collected}
    result = _compiled_agent_graph.invoke(initial_state, config={"recursion_limit": recursion_limit})
    return result["messages"][-1]


def extract_text(content):
    """Gemini's response content can come back as a plain string or as a
    list of content blocks (text/thinking/signature parts) depending on
    the model — normalize either shape to a plain string for display."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type", "text") == "text" and "text" in block:
                parts.append(block["text"])
        return "".join(parts)
    return str(content)


def infer_routed_to(collected):
    called = collected.get("tools_called") or []
    if not called:
        return "orchestrator"
    return TOOL_TO_AGENT.get(called[-1], "orchestrator")
