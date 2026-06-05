from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List

from graph.pipeline_trace import append_trace, trace_node
from tools.path_utils import normalize_package_path
from tools.railpack_tools import (
    get_railpack_version,
    load_json_file,
    run_railpack_prepare,
)
from tools.workspace_context import resolve_railpack_target, target_to_meta


def _ensure_unit_artifacts(unit: Dict[str, Any]) -> Dict[str, Any]:
    artifacts = unit.setdefault("artifacts", {})
    if not isinstance(artifacts, dict):
        artifacts = {}
        unit["artifacts"] = artifacts
    artifacts.setdefault("railpack_plan", None)
    artifacts.setdefault("railpack_json", None)
    return artifacts


def railpack_prepare_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Run railpack prepare for each deploy unit and store plans in artifacts."""
    with trace_node(state, "railpack_prepare"):
        if state.get("error"):
            return state

        repo_dir = state.get("repo_dir")
        if not repo_dir or not os.path.isdir(repo_dir):
            state["error"] = "Missing repo_dir; clone step must run first."
            return state

        units: List[Dict[str, Any]] = state.get("deploy_units") or []
        if not units:
            state["error"] = "No deploy units to prepare."
            return state

        state["railpack_version"] = get_railpack_version()

        plan_parent = state.get("_plan_tmp_parent")
        if not plan_parent:
            plan_parent = tempfile.mkdtemp(prefix="sd-railpack-plans-")
            state["_plan_tmp_parent"] = plan_parent

        for unit in units:
            if not isinstance(unit, dict):
                continue
            unit_root = normalize_package_path(unit.get("root", "."))
            unit_name = unit.get("name", unit_root)
            target = resolve_railpack_target(repo_dir, unit_root)
            unit["railpack_target"] = target_to_meta(target)

            if unit.get("type") == "existing_docker":
                append_trace(
                    state,
                    "railpack_prepare",
                    "skipped",
                    meta={"unit": unit_name, "reason": "existing_docker"},
                )
                continue

            plan_path = os.path.join(plan_parent, f"{unit_name}-railpack-plan.json")
            exit_code, logs = run_railpack_prepare(
                target.railpack_dir,
                plan_path,
                env_overrides={},
                build_cmd=target.build_cmd,
                start_cmd=target.start_cmd,
            )
            artifacts = _ensure_unit_artifacts(unit)

            if exit_code == 127:
                state["build_verification"] = {
                    "backend": "railpack",
                    "status": "unavailable",
                    "message": logs,
                    "attempts": 0,
                    "duration_seconds": 0.0,
                    "log_excerpt": logs,
                }
                append_trace(
                    state,
                    "railpack_prepare",
                    "unavailable",
                    meta={"unit": unit_name},
                    error=logs,
                )
                continue

            if exit_code != 0:
                state.setdefault("errors", []).append(
                    f"railpack prepare failed for {unit_name}: {logs[-500:]}"
                )
                append_trace(
                    state,
                    "railpack_prepare",
                    "error",
                    meta={"unit": unit_name, "exit_code": exit_code},
                    error=logs[-500:],
                )
                continue

            plan = load_json_file(plan_path)
            artifacts["railpack_plan"] = plan
            append_trace(
                state,
                "railpack_prepare",
                "ok",
                meta={"unit": unit_name, "plan_path": plan_path},
            )

        return state
