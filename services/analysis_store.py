from __future__ import annotations

from copy import deepcopy
from typing import Any


class AnalysisStoreError(Exception):
    """Base error for analysis storage reads."""


class AnalysisStoreNotConfiguredError(AnalysisStoreError):
    """Raised when Supabase is unavailable."""


class AnalysisStoreNotFoundError(AnalysisStoreError):
    """Raised when a requested row does not exist."""


def _get_supabase_client(provided_client: Any = None) -> Any:
    if provided_client is not None:
        return provided_client

    from db import supabase

    if not supabase:
        raise AnalysisStoreNotConfiguredError("Supabase is not configured")
    return supabase


def normalize_package_path(path: str | None) -> str:
    """Normalize package paths to the same stable form used by cache reads."""
    normalized = (path or ".").replace("\\", "/").strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized or "."


def _sanitize_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        sanitized = deepcopy(payload)
        sanitized.pop("_cache_package_path", None)
        return sanitized
    return payload


def get_analysis_response_by_id(response_id: str, supabase_client: Any = None) -> dict[str, Any]:
    supabase = _get_supabase_client(supabase_client)

    try:
        response = (
            supabase.table("analysis_responses")
            .select(
                "id,endpoint,repo_url,commit_sha,package_path,from_cache,passed,payload,created_at,"
                "schema_version,build_status,deploy_shape,railpack_version"
            )
            .eq("id", response_id)
            .single()
            .execute()
        )
    except Exception as exc:
        raise AnalysisStoreNotFoundError(f"Analysis response not found: {response_id}") from exc

    row = response.data or {}
    if not row:
        raise AnalysisStoreNotFoundError(f"Analysis response not found: {response_id}")

    return {
        "id": row.get("id"),
        "endpoint": row.get("endpoint"),
        "repo_url": row.get("repo_url"),
        "commit_sha": row.get("commit_sha"),
        "package_path": normalize_package_path(row.get("package_path")),
        "from_cache": bool(row.get("from_cache", False)),
        "passed": bool(row.get("passed", False)),
        "created_at": row.get("created_at"),
        "schema_version": row.get("schema_version"),
        "build_status": row.get("build_status"),
        "deploy_shape": row.get("deploy_shape"),
        "railpack_version": row.get("railpack_version"),
        "payload": _sanitize_payload(row.get("payload")),
    }


def get_analysis_cache_entry(
    repo_url: str,
    commit_sha: str,
    package_path: str = ".",
    supabase_client: Any = None,
) -> dict[str, Any]:
    supabase = _get_supabase_client(supabase_client)
    normalized_package_path = normalize_package_path(package_path)

    try:
        response = (
            supabase.table("analysis_cache")
            .select(
                "response_id,repo_url,commit_sha,package_path,result,created_at,"
                "schema_version,build_status,deploy_shape,railpack_version,pipeline_duration_ms,workflow_version"
            )
            .eq("repo_url", repo_url)
            .eq("commit_sha", commit_sha)
            .eq("package_path", normalized_package_path)
            .single()
            .execute()
        )
    except Exception as exc:
        raise AnalysisStoreNotFoundError(
            f"Analysis cache not found for {repo_url}@{commit_sha}"
        ) from exc

    row = response.data or {}
    if not row:
        raise AnalysisStoreNotFoundError(f"Analysis cache not found for {repo_url}@{commit_sha}")

    return {
        "response_id": row.get("response_id"),
        "repo_url": row.get("repo_url"),
        "commit_sha": row.get("commit_sha"),
        "package_path": normalize_package_path(row.get("package_path")),
        "created_at": row.get("created_at"),
        "schema_version": row.get("schema_version"),
        "build_status": row.get("build_status"),
        "deploy_shape": row.get("deploy_shape"),
        "railpack_version": row.get("railpack_version"),
        "pipeline_duration_ms": row.get("pipeline_duration_ms"),
        "workflow_version": row.get("workflow_version"),
        "result": _sanitize_payload(row.get("result")),
    }
