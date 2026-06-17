from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from graph.llm_retry import invoke_with_retry
from graph.pipeline_trace import append_trace, trace_node
from tools.path_utils import normalize_package_path
from tools.railpack_tools import (
    env_bool,
    env_int,
    get_railpack_version,
    load_json_file,
    merge_railpack_json,
    run_railpack_build,
    run_railpack_prepare,
)
from tools.remote_builds import remote_builds_enabled, run_remote_railpack_builds
from tools.workspace_context import resolve_railpack_target, target_to_meta


class RailpackRepairPatch(BaseModel):
    diagnosis: str = ""
    should_retry: bool = True
    railpack_json: Optional[Dict[str, Any]] = None
    env_overrides: Dict[str, str] = Field(default_factory=dict)
    give_up_reason: Optional[str] = None


def _truncate_logs(logs: str, max_chars: Optional[int] = None) -> str:
    limit = max_chars or env_int("SD_RAILPACK_VERIFY_MAX_LOG_CHARS", 8000, minimum=500)
    if len(logs) <= limit:
        return logs
    return logs[-limit:]


def _ensure_unit_artifacts(unit: Dict[str, Any]) -> Dict[str, Any]:
    artifacts = unit.setdefault("artifacts", {})
    if not isinstance(artifacts, dict):
        artifacts = {}
        unit["artifacts"] = artifacts
    return artifacts


def _scoped_key_files_for_unit(scan: Dict[str, Any], unit_root: str) -> Dict[str, str]:
    key_files = scan.get("key_files", {})
    if not isinstance(key_files, dict):
        return {}
    unit_norm = normalize_package_path(unit_root)
    scoped: Dict[str, str] = {}
    for path, content in key_files.items():
        norm = normalize_package_path(str(path))
        if unit_norm == "." or norm == unit_norm or norm.startswith(unit_norm + "/"):
            rel = norm if unit_norm == "." else norm[len(unit_norm) + 1:]
            scoped[rel or "."] = content
    return scoped


def _build_repair_prompt(
    unit: Dict[str, Any],
    plan: Optional[Dict[str, Any]],
    logs: str,
    key_files: Dict[str, str],
    attempt_history: List[Dict[str, Any]],
    feedback: str = "",
) -> str:
    history_blob = json.dumps(attempt_history[-3:], indent=2) if attempt_history else "[]"
    pkg_excerpt = key_files.get("package.json", "")[:4000]
    plan_blob = json.dumps(plan or {}, indent=2)[:6000]
    feedback_section = ""
    if feedback.strip():
        feedback_section = f"\nUSER FEEDBACK (apply when choosing patches):\n{feedback.strip()}\n"
    return f"""You are a Railpack build repair agent. Diagnose the failed build and emit a small structured patch.

Return ONLY raw JSON matching this schema:
{{
  "diagnosis": "string",
  "should_retry": boolean,
  "railpack_json": object or null,
  "env_overrides": {{ "RAILPACK_INSTALL_CMD": "..." }},
  "give_up_reason": "string or null"
}}

Rules:
- Patch railpack.json overrides or RAILPACK_* env vars only — do not rewrite the full plan.
- Set should_retry=false for user code syntax errors, test failures, or missing secrets.
- Do not repeat a patch already present in attempt_history.

Unit: {unit.get("name")} ({unit.get("root")})
Provider: {unit.get("provider")} Framework: {unit.get("framework")}

package.json excerpt:
{pkg_excerpt}

Current railpack plan:
{plan_blob}

Build log (tail):
{_truncate_logs(logs)}

attempt_history:
{history_blob}
{feedback_section}"""


def _invoke_repair_llm(state: Dict[str, Any], prompt: str) -> RailpackRepairPatch:
    from graph.nodes.llm_config import FALLBACK_PROMPTS, RETRY_CONFIGS, llm_repair, strip_markdown_wrapper

    def _validate(raw: Any) -> RailpackRepairPatch:
        if isinstance(raw, RailpackRepairPatch):
            return raw
        if hasattr(raw, "content"):
            content = strip_markdown_wrapper(str(raw.content), lang="json")
            data = json.loads(content.strip())
            return RailpackRepairPatch.model_validate(data)
        raise ValueError("Unexpected LLM response format")

    patch, _attempts, _fallback_used = invoke_with_retry(
        invoke_fn=lambda p: llm_repair.invoke(p),
        prompt=prompt,
        validator=_validate,
        fallback_prompt=FALLBACK_PROMPTS.get("repair"),
        config=RETRY_CONFIGS.get("repair"),
        node_name="railpack_build_repair",
    )
    llm_outputs = state.setdefault("llm_outputs", {})
    llm_outputs["repair"] = patch.model_dump()
    return patch


def _aggregate_build_status(unit_results: List[str]) -> str:
    if not unit_results:
        return "not_run"
    if all(r == "passed" for r in unit_results):
        return "passed"
    if all(r in {"skipped", "not_run"} for r in unit_results):
        return "skipped"
    if any(r == "passed" for r in unit_results) and any(r == "failed" for r in unit_results):
        return "partial"
    if any(r == "failed" for r in unit_results):
        return "failed"
    return "not_run"


def _build_verification_skip_reason() -> Optional[str]:
    raw = os.getenv("SD_SKIP_RAILPACK_BUILD")
    if raw is not None:
        return "SD_SKIP_RAILPACK_BUILD=true" if env_bool("SD_SKIP_RAILPACK_BUILD", False) else None
    if env_bool("RENDER", False):
        return "Render native runtime detected; BuildKit-backed railpack build is disabled"
    return None


def railpack_build_repair_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Build each deploy unit with up to 3 repair attempts on failure."""
    with trace_node(state, "railpack_build_repair"):
        if state.get("error"):
            return state

        repo_dir = state.get("repo_dir")
        if not repo_dir or not os.path.isdir(repo_dir):
            state["error"] = "Missing repo_dir; cannot run builds."
            return state

        if remote_builds_enabled():
            out = run_remote_railpack_builds(state)
            append_trace(
                out,
                "railpack_build_repair",
                out.get("build_status", "not_run"),
                meta={"backend": "aws_codebuild_railpack"},
            )
            return out

        skip_reason = _build_verification_skip_reason()
        if skip_reason:
            state["build_status"] = "skipped"
            state["build_verification"] = {
                "backend": "railpack",
                "status": "skipped",
                "message": f"Railpack build verification skipped: {skip_reason}.",
                "attempts": 0,
                "duration_seconds": 0.0,
                "log_excerpt": "",
            }
            append_trace(
                state,
                "railpack_build_repair",
                "skipped",
                meta={"reason": skip_reason},
            )
            return state

        units: List[Dict[str, Any]] = state.get("deploy_units") or []
        scan = state.get("repo_scan", {})
        repair_history: List[Dict[str, Any]] = state.setdefault("repair_history", [])
        max_attempts = env_int("SD_RAILPACK_REPAIR_MAX_ATTEMPTS", 3)
        max_log_chars = env_int("SD_RAILPACK_VERIFY_MAX_LOG_CHARS", 8000, minimum=500)
        started = time.monotonic()
        unit_statuses: List[str] = []
        total_attempts = 0

        railpack_version = get_railpack_version()
        state["railpack_version"] = railpack_version

        if railpack_version is None:
            state["build_verification"] = {
                "backend": "railpack",
                "status": "unavailable",
                "message": "Railpack CLI is not installed on this host.",
                "attempts": 0,
                "duration_seconds": 0.0,
                "log_excerpt": "",
            }
            state["build_status"] = "skipped"
            append_trace(state, "railpack_build_repair", "unavailable", error="railpack not installed")
            return state

        plan_parent = state.get("_plan_tmp_parent") or os.path.join(repo_dir, ".sd-plans")
        os.makedirs(plan_parent, exist_ok=True)
        last_logs = ""

        for unit in units:
            if not isinstance(unit, dict):
                continue

            unit_root = normalize_package_path(unit.get("root", "."))
            unit_name = unit.get("name", unit_root)
            target = resolve_railpack_target(repo_dir, unit_root)
            unit["railpack_target"] = target_to_meta(target)
            artifacts = _ensure_unit_artifacts(unit)

            if unit.get("type") == "existing_docker":
                unit_statuses.append("skipped")
                append_trace(
                    state,
                    "railpack_build_repair",
                    "skipped",
                    meta={"unit": unit_name, "reason": "existing_docker"},
                )
                continue

            env_overrides: Dict[str, str] = {}
            unit_passed = False
            last_logs = ""

            for attempt in range(1, max_attempts + 1):
                total_attempts += 1
                plan_path = os.path.join(plan_parent, f"{unit_name}-railpack-plan.json")
                prep_code, prep_logs = run_railpack_prepare(
                    target.railpack_dir,
                    plan_path,
                    env_overrides,
                    build_cmd=target.build_cmd,
                    start_cmd=target.start_cmd,
                )
                if prep_code == 0:
                    artifacts["railpack_plan"] = load_json_file(plan_path)

                exit_code, logs = run_railpack_build(
                    target.railpack_dir,
                    env_overrides,
                    build_cmd=target.build_cmd,
                    start_cmd=target.start_cmd,
                )
                last_logs = _truncate_logs(logs, max_log_chars)
                attempt_started = time.monotonic()

                if exit_code == 0:
                    unit_passed = True
                    repair_history.append({
                        "attempt": attempt,
                        "unit_name": unit_name,
                        "diagnosis": "Build succeeded",
                        "patch": {},
                        "railpack_json_after_merge": artifacts.get("railpack_json"),
                        "build_log_excerpt": last_logs,
                        "build_exit_code": exit_code,
                        "duration_seconds": round(time.monotonic() - attempt_started, 2),
                        "result": "passed",
                    })
                    break

                unit_attempt_history = [
                    entry for entry in repair_history if entry.get("unit_name") == unit_name
                ]
                prompt = _build_repair_prompt(
                    unit=unit,
                    plan=artifacts.get("railpack_plan"),
                    logs=logs,
                    key_files=_scoped_key_files_for_unit(scan, unit_root),
                    attempt_history=unit_attempt_history,
                    feedback=str(state.get("feedback") or ""),
                )

                try:
                    patch = _invoke_repair_llm(state, prompt)
                except Exception as exc:
                    repair_history.append({
                        "attempt": attempt,
                        "unit_name": unit_name,
                        "diagnosis": f"Repair LLM failed: {exc}",
                        "patch": {},
                        "build_log_excerpt": last_logs,
                        "build_exit_code": exit_code,
                        "duration_seconds": round(time.monotonic() - attempt_started, 2),
                        "result": "failed",
                    })
                    break

                merged_json = None
                if patch.railpack_json:
                    merged_json = merge_railpack_json(target.config_dir, patch.railpack_json)
                    artifacts["railpack_json"] = merged_json
                if patch.env_overrides:
                    env_overrides.update(patch.env_overrides)

                repair_entry = {
                    "attempt": attempt,
                    "unit_name": unit_name,
                    "diagnosis": patch.diagnosis,
                    "patch": {
                        "railpack_json": patch.railpack_json,
                        "env_overrides": patch.env_overrides,
                        "should_retry": patch.should_retry,
                        "give_up_reason": patch.give_up_reason,
                    },
                    "railpack_json_after_merge": merged_json,
                    "build_log_excerpt": last_logs,
                    "build_exit_code": exit_code,
                    "duration_seconds": round(time.monotonic() - attempt_started, 2),
                    "result": "failed",
                }
                repair_history.append(repair_entry)

                if not patch.should_retry or attempt >= max_attempts:
                    break

            unit_statuses.append("passed" if unit_passed else "failed")
            append_trace(
                state,
                "railpack_build_repair",
                "ok" if unit_passed else "error",
                meta={"unit": unit_name, "passed": unit_passed},
                error=None if unit_passed else last_logs[-300:],
            )

        duration = round(time.monotonic() - started, 2)
        build_status = _aggregate_build_status(unit_statuses)
        state["build_status"] = build_status
        state["build_verification"] = {
            "backend": "railpack",
            "status": build_status,
            "message": "Railpack build verification complete.",
            "attempts": total_attempts,
            "duration_seconds": duration,
            "log_excerpt": last_logs if unit_statuses else "",
        }
        return state
