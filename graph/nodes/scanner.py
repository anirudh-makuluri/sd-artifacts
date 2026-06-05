from typing import Dict, Any
import os
import time

from tools.github_tools import fetch_repo_structure
from tools.path_utils import normalize_package_path
from db import supabase


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value)


def _maybe_build_scope_guard_error(scan: Dict[str, Any], package_path: str) -> Dict[str, Any] | None:
    if not _env_bool("SD_SCOPE_GUARD_ENABLED", True):
        return None
    if normalize_package_path(package_path) != ".":
        return None

    tree_threshold = _env_int("SD_SCOPE_GUARD_TREE_THRESHOLD", 3000)
    package_threshold = _env_int("SD_SCOPE_GUARD_PACKAGE_THRESHOLD", 20)
    tree_count = int(scan.get("tree_entry_count") or 0)
    candidate_paths = scan.get("candidate_package_paths") or []
    if not isinstance(candidate_paths, list):
        candidate_paths = []
    candidate_count = len(candidate_paths)

    if tree_count <= tree_threshold and candidate_count <= package_threshold:
        return None

    return {
        "code": "scope_required",
        "reason": (
            "Repository scope is too broad for root analysis. "
            "Specify package_path to narrow analysis."
        ),
        "tree_entry_count": tree_count,
        "candidate_package_count": candidate_count,
        "suggested_package_paths": candidate_paths[:10],
    }


def _is_v2_cache_result(result: Dict[str, Any]) -> bool:
    try:
        return int(result.get("schema_version", 0)) == 2
    except (TypeError, ValueError):
        return False


def scanner_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Calls GitHub tool to populate repo_scan. Checks v2 schema cache only."""
    scan = fetch_repo_structure.invoke({
        "repo_url": state["repo_url"],
        "github_token": state.get("github_token"),
        "max_files": state.get("max_files", 50),
        "package_path": state.get("package_path", "."),
    })

    if "error" in scan:
        state["error"] = scan["error"]
        return state

    commit_sha = scan.get("commit_sha", "unknown")
    state["commit_sha"] = commit_sha
    requested_package_path = normalize_package_path(state.get("package_path", "."))

    scope_guard_error = _maybe_build_scope_guard_error(scan=scan, package_path=requested_package_path)
    if scope_guard_error:
        state["error"] = scope_guard_error
        state["repo_scan"] = scan
        return state

    if supabase and commit_sha != "unknown" and not state.get("_skip_cache"):
        for attempt in range(3):
            try:
                response = (
                    supabase.table("analysis_cache")
                    .select("result")
                    .eq("repo_url", state["repo_url"])
                    .eq("commit_sha", commit_sha)
                    .eq("package_path", requested_package_path)
                    .execute()
                )
                if response.data:
                    for row in response.data:
                        cached = row.get("result", {}) if isinstance(row, dict) else {}
                        if isinstance(cached, dict) and _is_v2_cache_result(cached):
                            state["cached_response"] = cached
                            state["repo_scan"] = scan
                            return state
                break
            except Exception as e:
                print(f"Supabase cache read error (attempt {attempt + 1}/3): {e}")
                if attempt < 2:
                    time.sleep(1)

    state["repo_scan"] = scan
    return state
