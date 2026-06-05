from langgraph.graph import StateGraph, END
from typing import Dict, Any

from .nodes import (
    scanner_node,
    clone_repo_node,
    classifier_node,
    railpack_prepare_node,
    deploy_briefing_node,
    railpack_build_repair_node,
    finalize_node,
)

workflow = StateGraph(dict)

workflow.add_node("scanner", scanner_node)
workflow.add_node("clone_repo", clone_repo_node)
workflow.add_node("classifier", classifier_node)
workflow.add_node("railpack_prepare", railpack_prepare_node)
workflow.add_node("deploy_briefing", deploy_briefing_node)
workflow.add_node("railpack_build_repair", railpack_build_repair_node)
workflow.add_node("finalize", finalize_node)


def check_scanner_error(state: Dict[str, Any]) -> str:
    """Route to END if scanner found an error or if cached_response is present."""
    if state.get("error") or state.get("cached_response"):
        return "error_or_cached"
    return "continue"


def check_fatal_error(state: Dict[str, Any]) -> str:
    """Route to END when a prior node set a fatal error."""
    return "error" if state.get("error") else "continue"


workflow.set_entry_point("scanner")

workflow.add_conditional_edges(
    "scanner",
    check_scanner_error,
    {
        "error_or_cached": END,
        "continue": "clone_repo",
    },
)

workflow.add_conditional_edges(
    "clone_repo",
    check_fatal_error,
    {
        "error": END,
        "continue": "classifier",
    },
)

workflow.add_conditional_edges(
    "classifier",
    check_fatal_error,
    {
        "error": END,
        "continue": "railpack_prepare",
    },
)

workflow.add_edge("railpack_prepare", "deploy_briefing")
workflow.add_edge("deploy_briefing", "railpack_build_repair")
workflow.add_edge("railpack_build_repair", "finalize")
workflow.add_edge("finalize", END)

graph = workflow.compile()
