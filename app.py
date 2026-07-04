from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from graph.graph import graph
from graph.nodes.llm_config import TokenTracker
from models.schemas import (
    SCHEMA_VERSION,
    AnalyzeResponse,
    BuildVerification,
    DeployUnit,
    PipelineTraceEntry,
    RepairAttempt,
    TokenUsage,
)
from tools.path_utils import normalize_package_path
from tools.railpack_tools import get_railpack_version

app = FastAPI(title="SD-Artifacts Repo Analyzer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

V2_PROGRESS_NODES = [
    "scanner",
    "clone_repo",
    "classifier",
    "railpack_prepare",
    "deploy_briefing",
    "railpack_build_repair",
    "finalize",
]


class AnalyzeRequest(BaseModel):
    repo_url: str
    github_token: Optional[str] = None
    max_files: Optional[int] = 50
    package_path: str = "."
    commit_sha: Optional[str] = None
    refresh: bool = False


class DeleteCacheRequest(BaseModel):
    repo_url: str
    commit_sha: Optional[str] = None
    package_path: str = "."


class DeleteCacheResponse(BaseModel):
    deleted: int
    repo_url: str
    commit_sha: Optional[str] = None
    package_path: Optional[str] = None


class FeedbackRequest(BaseModel):
    repo_url: str
    commit_sha: str
    package_path: str = "."
    feedback: str
    github_token: Optional[str] = None


class ResponseStatusRequest(BaseModel):
    response_id: str
    passed: bool


class ResponseStatusResponse(BaseModel):
    response_id: str
    passed: bool
    cache_deleted: int = 0


class HealthResponse(BaseModel):
    status: str
    scope: str
    supabase_configured: bool
    railpack_configured: bool


def _build_health_response(*, scope: str, supabase: Any) -> HealthResponse:
    return HealthResponse(
        status="ok",
        scope=scope,
        supabase_configured=bool(supabase),
        railpack_configured=bool(get_railpack_version()),
    )


def build_analyze_response(
    state: Dict[str, Any],
    response_id: str,
    token_usage: TokenUsage | Dict[str, Any],
) -> AnalyzeResponse:
    if not isinstance(token_usage, TokenUsage):
        token_usage = TokenUsage(**(token_usage or {}))

    errors: List[str] = list(state.get("errors") or [])
    if state.get("error"):
        err = state["error"]
        if isinstance(err, dict):
            errors.append(err.get("reason") or json.dumps(err))
        else:
            errors.append(str(err))

    deploy_units: List[DeployUnit] = []
    for unit in state.get("deploy_units") or []:
        deploy_units.append(DeployUnit(**unit) if isinstance(unit, dict) else unit)

    repair_history: List[RepairAttempt] = []
    for entry in state.get("repair_history") or []:
        repair_history.append(RepairAttempt(**entry) if isinstance(entry, dict) else entry)

    pipeline_trace: List[PipelineTraceEntry] = []
    for entry in state.get("pipeline_trace") or []:
        pipeline_trace.append(PipelineTraceEntry(**entry) if isinstance(entry, dict) else entry)

    build_verification = state.get("build_verification") or {}
    if not isinstance(build_verification, BuildVerification):
        build_verification = BuildVerification(**build_verification)

    return AnalyzeResponse(
        schema_version=int(state.get("schema_version") or SCHEMA_VERSION),
        response_id=response_id,
        commit_sha=state.get("commit_sha", "unknown"),
        package_path=normalize_package_path(state.get("package_path", ".")),
        deploy_shape=state.get("deploy_shape", "server"),
        railpack_version=state.get("railpack_version"),
        workflow_version=state.get("workflow_version"),
        build_status=state.get("build_status", "not_run"),
        deploy_units=deploy_units,
        remote_builds=state.get("remote_builds") or {},
        deploy_briefing=state.get("deploy_briefing", ""),
        build_verification=build_verification,
        repair_history=repair_history,
        pipeline_trace=pipeline_trace,
        errors=errors,
        llm_outputs=state.get("llm_outputs") or {},
        inputs_snapshot=state.get("inputs_snapshot") or {},
        token_usage=token_usage,
    )


def _is_v2_cache_result(result: Dict[str, Any]) -> bool:
    try:
        return int(result.get("schema_version", 0)) == SCHEMA_VERSION
    except (TypeError, ValueError):
        return False


def _store_response_log(
    supabase,
    *,
    response_id: str,
    endpoint: str,
    repo_url: str,
    commit_sha: Optional[str],
    package_path: str,
    from_cache: bool,
    payload: Dict,
    schema_version: int = SCHEMA_VERSION,
    build_status: Optional[str] = None,
    deploy_shape: Optional[str] = None,
    railpack_version: Optional[str] = None,
) -> None:
    if not supabase:
        return
    row = {
        "id": response_id,
        "endpoint": endpoint,
        "repo_url": repo_url,
        "commit_sha": commit_sha,
        "package_path": package_path or ".",
        "from_cache": from_cache,
        "payload": payload,
        "schema_version": schema_version,
        "build_status": build_status or payload.get("build_status", "not_run"),
        "deploy_shape": deploy_shape or payload.get("deploy_shape"),
        "railpack_version": railpack_version or payload.get("railpack_version"),
    }
    for attempt in range(3):
        try:
            supabase.table("analysis_responses").insert(row).execute()
            break
        except Exception as e:
            print(f"Failed to store analysis response log (attempt {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(1)


def _fetch_cached_analysis(
    repo_url: str,
    commit_sha: str,
    package_path: str = ".",
):
    from db import supabase

    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase is not configured")

    normalized_path = normalize_package_path(package_path)
    try:
        existing = (
            supabase.table("analysis_cache")
            .select("result,response_id")
            .eq("repo_url", repo_url)
            .eq("commit_sha", commit_sha)
            .eq("package_path", normalized_path)
            .single()
            .execute()
        )
    except Exception:
        raise HTTPException(
            status_code=404,
            detail=f"No cached v2 analysis found for {repo_url}@{commit_sha}",
        )

    cached_result = existing.data.get("result") if existing.data else None
    if not cached_result or not _is_v2_cache_result(cached_result):
        raise HTTPException(
            status_code=404,
            detail=f"No cached v2 analysis found for {repo_url}@{commit_sha}",
        )

    response_id = existing.data.get("response_id") if existing.data else None
    if response_id and isinstance(cached_result, dict):
        cached_result.setdefault("response_id", response_id)

    return supabase, cached_result


def _cache_insert_row(
    response: AnalyzeResponse,
    state: Dict[str, Any],
    *,
    repo_url: str,
    commit_sha: str,
    package_path: str,
    response_id: str,
) -> Dict[str, Any]:
    result_dict = response.model_dump()
    return {
        "response_id": response_id,
        "repo_url": repo_url,
        "commit_sha": commit_sha,
        "package_path": normalize_package_path(package_path),
        "schema_version": response.schema_version,
        "build_status": response.build_status,
        "deploy_shape": response.deploy_shape,
        "railpack_version": response.railpack_version,
        "pipeline_duration_ms": state.get("pipeline_duration_ms"),
        "workflow_version": response.workflow_version,
        "result": result_dict,
    }


def _save_to_cache(supabase, row: Dict[str, Any], *, replace: bool = False) -> None:
    if not supabase:
        return
    for attempt in range(3):
        try:
            if replace:
                supabase.table("analysis_cache").upsert(
                    row,
                    on_conflict="repo_url,commit_sha,package_path",
                ).execute()
            else:
                supabase.table("analysis_cache").insert(row).execute()
            break
        except Exception as e:
            print(f"Failed to cache result in Supabase (attempt {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(1)


def _require_auth(authorization: Optional[str]) -> None:
    expected = os.getenv("SD_API_BEARER_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="API authentication is not configured on the server")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split(" ", 1)[1].strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def require_auth(authorization: Optional[str] = Header(default=None)) -> None:
    _require_auth(authorization)


def _graph_initial_state(req: AnalyzeRequest) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "repo_url": req.repo_url,
        "github_token": req.github_token,
        "max_files": req.max_files,
        "package_path": req.package_path,
    }
    if req.refresh:
        state["_skip_cache"] = True
    return state


def _handle_graph_error(result: Dict[str, Any]) -> None:
    if "error" not in result:
        return
    err = result["error"]
    if isinstance(err, dict):
        raise HTTPException(status_code=400, detail=err)
    raise HTTPException(status_code=400, detail=str(err))


@app.get("/health", response_model=HealthResponse)
async def health_check():
    from db import supabase

    return _build_health_response(scope="public", supabase=supabase)


@app.get("/healthz", response_model=HealthResponse, dependencies=[Depends(require_auth)])
async def health_check_authenticated():
    from db import supabase

    return _build_health_response(scope="authenticated", supabase=supabase)


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze_repo(req: AnalyzeRequest, authorization: Optional[str] = Header(default=None)):
    from db import supabase

    _require_auth(authorization)
    if req.commit_sha and not req.refresh:
        try:
            _supabase, cached_result = _fetch_cached_analysis(
                repo_url=req.repo_url,
                commit_sha=req.commit_sha,
                package_path=req.package_path,
            )
            cached_payload = dict(cached_result)
            cached_payload.setdefault("commit_sha", req.commit_sha)
            response_id = str(uuid4())
            cached_payload["response_id"] = response_id
            _store_response_log(
                supabase,
                response_id=response_id,
                endpoint="/analyze",
                repo_url=req.repo_url,
                commit_sha=req.commit_sha,
                package_path=req.package_path,
                from_cache=True,
                payload=cached_payload,
            )
            return AnalyzeResponse(**cached_payload)
        except HTTPException as e:
            if e.status_code != 404:
                raise

    tracker = TokenTracker()
    result = graph.invoke(_graph_initial_state(req), config={"callbacks": [tracker]})
    _handle_graph_error(result)

    if "cached_response" in result:
        cached_payload = dict(result["cached_response"])
        cached_payload.setdefault("commit_sha", result.get("commit_sha", "unknown"))
        response_id = str(uuid4())
        cached_payload["response_id"] = response_id
        _store_response_log(
            supabase,
            response_id=response_id,
            endpoint="/analyze",
            repo_url=req.repo_url,
            commit_sha=cached_payload.get("commit_sha"),
            package_path=req.package_path,
            from_cache=True,
            payload=cached_payload,
            build_status=cached_payload.get("build_status"),
            deploy_shape=cached_payload.get("deploy_shape"),
            railpack_version=cached_payload.get("railpack_version"),
        )
        return AnalyzeResponse(**cached_payload)

    commit_sha = result.get("commit_sha", "unknown")
    response_id = str(uuid4())
    response = build_analyze_response(result, response_id, tracker.get_usage())

    if supabase and commit_sha != "unknown":
        _save_to_cache(
            supabase,
            _cache_insert_row(
                response,
                result,
                repo_url=req.repo_url,
                commit_sha=commit_sha,
                package_path=req.package_path,
                response_id=response_id,
            ),
            replace=req.refresh,
        )

    response_payload = response.model_dump()
    _store_response_log(
        supabase,
        response_id=response_id,
        endpoint="/analyze",
        repo_url=req.repo_url,
        commit_sha=commit_sha,
        package_path=req.package_path,
        from_cache=False,
        payload=response_payload,
        build_status=response.build_status,
        deploy_shape=response.deploy_shape,
        railpack_version=response.railpack_version,
    )
    return response


@app.delete("/cache", response_model=DeleteCacheResponse, dependencies=[Depends(require_auth)])
async def delete_cached_analysis(req: DeleteCacheRequest):
    from db import supabase

    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase is not configured")

    try:
        query = supabase.table("analysis_cache").select("id").eq("repo_url", req.repo_url)
        if req.commit_sha:
            query = query.eq("commit_sha", req.commit_sha)
        if req.package_path:
            query = query.eq("package_path", normalize_package_path(req.package_path))

        existing = query.execute()
        rows = existing.data or []
        if not rows:
            raise HTTPException(status_code=404, detail="No cached result found for the provided criteria")

        delete_query = supabase.table("analysis_cache").delete().eq("repo_url", req.repo_url)
        if req.commit_sha:
            delete_query = delete_query.eq("commit_sha", req.commit_sha)
        if req.package_path:
            delete_query = delete_query.eq("package_path", normalize_package_path(req.package_path))
        delete_query.execute()

        return DeleteCacheResponse(
            deleted=len(rows),
            repo_url=req.repo_url,
            commit_sha=req.commit_sha,
            package_path=req.package_path,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete cache: {e}")


@app.post("/analyze/stream")
async def analyze_repo_stream(req: AnalyzeRequest, authorization: Optional[str] = Header(default=None)):
    from db import supabase

    async def cached_event_generator(cached_payload: Dict):
        import asyncio
        import random

        total_delay_s = random.uniform(4.0, 10.0)
        step_delay_s = total_delay_s / max(1, len(V2_PROGRESS_NODES))
        for node in V2_PROGRESS_NODES:
            await asyncio.sleep(step_delay_s)
            yield f"event: progress\ndata: {json.dumps({'node': node, 'status': 'completed'})}\n\n"

        _store_response_log(
            supabase,
            response_id=cached_payload.get("response_id") or str(uuid4()),
            endpoint="/analyze/stream",
            repo_url=req.repo_url,
            commit_sha=cached_payload.get("commit_sha"),
            package_path=req.package_path,
            from_cache=True,
            payload=cached_payload,
        )
        yield f"event: complete\ndata: {json.dumps(cached_payload)}\n\n"

    async def live_event_generator():
        import asyncio
        import random

        tracker = TokenTracker()
        initial_state = _graph_initial_state(req)

        try:
            full_state: Dict[str, Any] = {}
            async for output in graph.astream(initial_state, config={"callbacks": [tracker]}):
                for node_name, state_update in output.items():
                    full_state.update(state_update)
                    yield f"event: progress\ndata: {json.dumps({'node': node_name, 'status': 'completed'})}\n\n"

                    if "error" in state_update:
                        err = state_update["error"]
                        detail = err if isinstance(err, (str, dict)) else str(err)
                        yield f"event: error\ndata: {json.dumps({'detail': detail})}\n\n"
                        return

                    if "cached_response" in state_update:
                        cached = dict(state_update["cached_response"])
                        if "token_usage" not in cached:
                            usage = TokenUsage(**tracker.get_usage())
                            cached["token_usage"] = usage.model_dump()
                        cached.setdefault(
                            "commit_sha",
                            state_update.get("commit_sha", full_state.get("commit_sha", "unknown")),
                        )
                        response_id = str(uuid4())
                        cached["response_id"] = response_id

                        total_delay_s = random.uniform(4.0, 10.0)
                        remaining = [n for n in V2_PROGRESS_NODES if n != "scanner"]
                        step_delay_s = total_delay_s / max(1, len(remaining))
                        for node in remaining:
                            await asyncio.sleep(step_delay_s)
                            yield f"event: progress\ndata: {json.dumps({'node': node, 'status': 'completed'})}\n\n"

                        _store_response_log(
                            supabase,
                            response_id=response_id,
                            endpoint="/analyze/stream",
                            repo_url=req.repo_url,
                            commit_sha=cached.get("commit_sha"),
                            package_path=req.package_path,
                            from_cache=True,
                            payload=cached,
                            build_status=cached.get("build_status"),
                            deploy_shape=cached.get("deploy_shape"),
                            railpack_version=cached.get("railpack_version"),
                        )
                        yield f"event: complete\ndata: {json.dumps(cached)}\n\n"
                        return

            response_id = str(uuid4())
            response = build_analyze_response(full_state, response_id, tracker.get_usage())
            commit_sha = full_state.get("commit_sha", "unknown")

            if supabase and commit_sha != "unknown":
                _save_to_cache(
                    supabase,
                    _cache_insert_row(
                        response,
                        full_state,
                        repo_url=req.repo_url,
                        commit_sha=commit_sha,
                        package_path=req.package_path,
                        response_id=response_id,
                    ),
                    replace=req.refresh,
                )

            final_dict = response.model_dump()
            _store_response_log(
                supabase,
                response_id=response_id,
                endpoint="/analyze/stream",
                repo_url=req.repo_url,
                commit_sha=commit_sha,
                package_path=req.package_path,
                from_cache=False,
                payload=final_dict,
                build_status=response.build_status,
                deploy_shape=response.deploy_shape,
                railpack_version=response.railpack_version,
            )
            yield f"event: complete\ndata: {json.dumps(final_dict)}\n\n"

        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'detail': str(e)})}\n\n"

    _require_auth(authorization)
    if req.commit_sha and not req.refresh:
        try:
            _supabase, cached_result = _fetch_cached_analysis(
                repo_url=req.repo_url,
                commit_sha=req.commit_sha,
                package_path=req.package_path,
            )
            cached_payload = dict(cached_result)
            cached_payload.setdefault("commit_sha", req.commit_sha)
            response_id = str(uuid4())
            cached_payload["response_id"] = response_id
            cached_payload.setdefault(
                "token_usage",
                {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            )
            return StreamingResponse(cached_event_generator(cached_payload), media_type="text/event-stream")
        except HTTPException as e:
            if e.status_code != 404:
                raise

    return StreamingResponse(live_event_generator(), media_type="text/event-stream")


@app.post("/feedback", response_model=AnalyzeResponse, dependencies=[Depends(require_auth)])
async def improve_with_feedback(req: FeedbackRequest):
    from graph.feedback import run_feedback_improvement

    supabase, cached_result = _fetch_cached_analysis(req.repo_url, req.commit_sha, req.package_path)

    tracker = TokenTracker()
    try:
        improved_state = run_feedback_improvement(
            cached_result,
            req.feedback,
            repo_url=req.repo_url,
            github_token=req.github_token,
            package_path=req.package_path,
            config={"callbacks": [tracker]},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Feedback improvement failed: {e}")

    response_id = str(uuid4())
    response = build_analyze_response(improved_state, response_id, tracker.get_usage())
    result_dict = response.model_dump()

    try:
        supabase.table("analysis_cache").upsert(
            _cache_insert_row(
                response,
                improved_state,
                repo_url=req.repo_url,
                commit_sha=req.commit_sha,
                package_path=req.package_path,
                response_id=response_id,
            ),
            on_conflict="repo_url,commit_sha,package_path",
        ).execute()
    except Exception as e:
        print(f"Failed to update cache after feedback improvement: {e}")

    _store_response_log(
        supabase,
        response_id=response_id,
        endpoint="/feedback",
        repo_url=req.repo_url,
        commit_sha=req.commit_sha,
        package_path=req.package_path,
        from_cache=False,
        payload=result_dict,
        build_status=response.build_status,
        deploy_shape=response.deploy_shape,
        railpack_version=response.railpack_version,
    )
    return response


@app.post("/feedback/stream", dependencies=[Depends(require_auth)])
async def improve_with_feedback_stream(req: FeedbackRequest):
    async def event_generator():
        from graph.feedback import build_feedback_initial_state, feedback_graph

        tracker = TokenTracker()

        try:
            supabase, cached_result = _fetch_cached_analysis(req.repo_url, req.commit_sha, req.package_path)
        except HTTPException as e:
            yield f"event: error\ndata: {json.dumps({'detail': e.detail})}\n\n"
            return

        initial_state = build_feedback_initial_state(
            cached_result,
            req.feedback,
            repo_url=req.repo_url,
            github_token=req.github_token,
            package_path=req.package_path,
        )

        try:
            full_state = dict(initial_state)
            async for output in feedback_graph.astream(initial_state, config={"callbacks": [tracker]}):
                for node_name, state_update in output.items():
                    full_state.update(state_update)
                    yield f"event: progress\ndata: {json.dumps({'node': node_name, 'status': 'completed'})}\n\n"

                    if "error" in state_update:
                        err = state_update["error"]
                        detail = err if isinstance(err, (str, dict)) else str(err)
                        yield f"event: error\ndata: {json.dumps({'detail': detail})}\n\n"
                        return

            response_id = str(uuid4())
            response = build_analyze_response(full_state, response_id, tracker.get_usage())
            result_dict = response.model_dump()

            try:
                supabase.table("analysis_cache").upsert(
                    _cache_insert_row(
                        response,
                        full_state,
                        repo_url=req.repo_url,
                        commit_sha=req.commit_sha,
                        package_path=req.package_path,
                        response_id=response_id,
                    ),
                    on_conflict="repo_url,commit_sha,package_path",
                ).execute()
            except Exception as e:
                print(f"Failed to update cache after feedback improvement: {e}")

            _store_response_log(
                supabase,
                response_id=response_id,
                endpoint="/feedback/stream",
                repo_url=req.repo_url,
                commit_sha=req.commit_sha,
                package_path=req.package_path,
                from_cache=False,
                payload=result_dict,
                build_status=response.build_status,
                deploy_shape=response.deploy_shape,
                railpack_version=response.railpack_version,
            )
            yield f"event: complete\ndata: {json.dumps(result_dict)}\n\n"

        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'detail': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/responses/status", response_model=ResponseStatusResponse, dependencies=[Depends(require_auth)])
async def set_response_status(req: ResponseStatusRequest):
    from db import supabase

    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase is not configured")

    try:
        response_row = (
            supabase.table("analysis_responses")
            .select("id,repo_url,commit_sha,package_path")
            .eq("id", req.response_id)
            .single()
            .execute()
        )
    except Exception:
        raise HTTPException(status_code=404, detail=f"Response id not found: {req.response_id}")

    row = response_row.data or {}
    if not row:
        raise HTTPException(status_code=404, detail=f"Response id not found: {req.response_id}")

    try:
        supabase.table("analysis_responses").update({"passed": req.passed}).eq("id", req.response_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update response status: {e}")

    deleted = 0
    if req.passed is False:
        deleted_rows = (
            supabase.table("analysis_cache")
            .delete()
            .eq("repo_url", row.get("repo_url"))
            .eq("commit_sha", row.get("commit_sha"))
            .eq("package_path", normalize_package_path(row.get("package_path")))
            .execute()
        )
        deleted = len(deleted_rows.data or [])

    return ResponseStatusResponse(response_id=req.response_id, passed=req.passed, cache_deleted=deleted)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
