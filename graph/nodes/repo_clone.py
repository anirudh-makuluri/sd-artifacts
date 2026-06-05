from __future__ import annotations

import os
import tempfile
from typing import Any, Dict

from graph.pipeline_trace import append_trace, trace_node
from tools.railpack_tools import clone_repository


def clone_repo_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Clone repository and store repo_dir in state."""
    with trace_node(state, "clone_repo"):
        if state.get("error"):
            return state

        repo_url = (state.get("repo_url") or "").strip()
        if not repo_url:
            state["error"] = "Missing repo_url; cannot clone repository."
            append_trace(state, "clone_repo", "error", error=state["error"])
            return state

        if state.get("repo_dir") and os.path.isdir(state["repo_dir"]):
            return state

        tmp_parent = state.get("_repo_tmp_parent")
        if not tmp_parent:
            tmp_parent = tempfile.mkdtemp(prefix="sd-artifacts-repo-")
            state["_repo_tmp_parent"] = tmp_parent

        repo_dir = os.path.join(tmp_parent, "repo")
        ok, err = clone_repository(
            repo_url=repo_url,
            github_token=state.get("github_token"),
            commit_sha=(state.get("commit_sha") or "").strip(),
            dest_dir=repo_dir,
        )
        if not ok:
            state["error"] = f"Failed to clone repository: {err[:500]}"
            append_trace(state, "clone_repo", "error", error=state["error"])
            return state

        state["repo_dir"] = repo_dir
        return state
