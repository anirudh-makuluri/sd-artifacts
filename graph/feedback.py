"""v2 feedback workflow: re-clone repo and re-run railpack build/repair with user feedback."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional

from langgraph.graph import END, StateGraph

from graph.graph import check_fatal_error
from graph.nodes.finalize import finalize_node
from graph.nodes.railpack_build_repair import railpack_build_repair_node
from graph.nodes.repo_clone import clone_repo_node
from tools.path_utils import normalize_package_path


def build_feedback_initial_state(
    cached_result: Dict[str, Any],
    feedback: str,
    *,
    repo_url: str,
    github_token: Optional[str] = None,
    package_path: str = ".",
) -> Dict[str, Any]:
    """Build graph state from a cached v2 analysis plus new user feedback."""
    normalized_path = normalize_package_path(package_path or cached_result.get("package_path", "."))
    return {
        "feedback": feedback,
        "repo_url": repo_url,
        "github_token": github_token,
        "commit_sha": cached_result.get("commit_sha", "unknown"),
        "package_path": normalized_path,
        "deploy_shape": cached_result.get("deploy_shape", "server"),
        "deploy_units": deepcopy(cached_result.get("deploy_units") or []),
        "deploy_briefing": cached_result.get("deploy_briefing", ""),
        "repo_scan": (cached_result.get("inputs_snapshot") or {}).get("repo_scan", {}),
        "inputs_snapshot": cached_result.get("inputs_snapshot") or {},
        "llm_outputs": {},
        "repair_history": [],
        "pipeline_trace": [],
        "_skip_cache": True,
    }


def run_feedback_improvement(
    cached_result: Dict[str, Any],
    feedback: str,
    *,
    repo_url: str,
    github_token: Optional[str] = None,
    package_path: str = ".",
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Re-run clone + railpack build/repair with feedback, returning graph state."""
    initial_state = build_feedback_initial_state(
        cached_result,
        feedback,
        repo_url=repo_url,
        github_token=github_token,
        package_path=package_path,
    )
    return feedback_graph.invoke(initial_state, config=config or {})


feedback_workflow = StateGraph(dict)
feedback_workflow.add_node("clone_repo", clone_repo_node)
feedback_workflow.add_node("railpack_build_repair", railpack_build_repair_node)
feedback_workflow.add_node("finalize", finalize_node)

feedback_workflow.set_entry_point("clone_repo")
feedback_workflow.add_conditional_edges(
    "clone_repo",
    check_fatal_error,
    {"error": END, "continue": "railpack_build_repair"},
)
feedback_workflow.add_conditional_edges(
    "railpack_build_repair",
    check_fatal_error,
    {"error": END, "continue": "finalize"},
)
feedback_workflow.add_edge("finalize", END)

feedback_graph = feedback_workflow.compile()
