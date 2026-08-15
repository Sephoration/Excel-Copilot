from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..config import settings
from ..services.llm_gateway import LLMGateway
from .nodes import (
    executor_node,
    planner_node,
    reflector_node,
    responder_node,
    visualizer_node,
)
from .state import AgentState


def build_graph(llm: LLMGateway) -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("planner", lambda state: planner_node(state, llm))
    graph.add_node("executor", executor_node)
    graph.add_node("reflector", lambda state: reflector_node(state, llm))
    graph.add_node("visualizer", visualizer_node)
    graph.add_node("responder", lambda state: responder_node(state, llm))

    graph.add_edge(START, "planner")
    graph.add_edge("planner", "executor")

    def route_after_executor(state: AgentState) -> str:
        if state.get("error"):
            if state.get("retries", 0) < settings.max_reflect_retries:
                return "reflector"
            return "responder"
        if state.get("plot"):
            return "visualizer"
        return "responder"

    def route_after_reflector(state: AgentState) -> str:
        if state.get("reply"):
            return "responder"
        return "executor"

    graph.add_conditional_edges(
        "executor",
        route_after_executor,
        {
            "reflector": "reflector",
            "visualizer": "visualizer",
            "responder": "responder",
        },
    )
    graph.add_conditional_edges(
        "reflector",
        route_after_reflector,
        {"executor": "executor", "responder": "responder"},
    )
    graph.add_edge("visualizer", "responder")
    graph.add_edge("responder", END)

    return graph.compile()
