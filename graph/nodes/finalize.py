from __future__ import annotations

import os
from typing import Any, Dict

from graph.pipeline_trace import append_trace, trace_node
from models.schemas import SCHEMA_VERSION


def _pipeline_duration_ms(state: Dict[str, Any]) -> int:
    trace = state.get("pipeline_trace") or []
    if not isinstance(trace, list):
        return 0
    return sum(int(entry.get("duration_ms") or 0) for entry in trace if isinstance(entry, dict))


def finalize_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Set build_status, schema_version, and workflow_version on the response state."""
    with trace_node(state, "finalize"):
        state["schema_version"] = SCHEMA_VERSION
        state["workflow_version"] = (
            os.getenv("SD_WORKFLOW_VERSION")
            or os.getenv("GIT_SHA")
            or "sd-artifacts@dev"
        )

        if not state.get("build_status"):
            verification = state.get("build_verification") or {}
            status = verification.get("status") if isinstance(verification, dict) else None
            state["build_status"] = status or ("error" if state.get("error") else "not_run")

        state["pipeline_duration_ms"] = _pipeline_duration_ms(state)

        if state.get("error") and state.get("build_status") not in {"failed", "partial", "error"}:
            state["build_status"] = "error"

        append_trace(
            state,
            "finalize",
            "ok",
            meta={
                "build_status": state.get("build_status"),
                "schema_version": state.get("schema_version"),
                "workflow_version": state.get("workflow_version"),
            },
        )
        return state
