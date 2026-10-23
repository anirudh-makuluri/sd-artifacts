"""Bound an SSE request without reporting a provider timeout as a build failure."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from typing import AsyncGenerator

logger = logging.getLogger("sd_artifacts.stream")
DEFAULT_STREAM_TIMEOUT_SECONDS = 110.0


def stream_timeout_seconds() -> float:
    raw = os.getenv("SD_ANALYZE_STREAM_TIMEOUT_SECONDS", "110")
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_STREAM_TIMEOUT_SECONDS
    return (
        value if math.isfinite(value) and value > 0 else DEFAULT_STREAM_TIMEOUT_SECONDS
    )


async def bounded_analysis_events(
    events: AsyncGenerator[str, None], *, request_id: str, timeout_seconds: float
) -> AsyncGenerator[str, None]:
    started = time.monotonic()
    workflow_version = (
        os.getenv("SD_WORKFLOW_VERSION") or os.getenv("GIT_SHA") or "sd-artifacts@dev"
    )
    last_completed_node = None
    outcome = "disconnected"
    logger.info(
        "analysis_stream_started request_id=%s timeout_seconds=%s workflow_version=%s",
        request_id,
        timeout_seconds,
        workflow_version,
    )
    try:
        while True:
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise asyncio.TimeoutError
            # This budget includes cache reads, graph execution, and persistence.
            try:
                event = await asyncio.wait_for(events.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                raise RuntimeError(
                    "Analysis stream ended without a terminal event"
                ) from None
            event_type = event.splitlines()[0].removeprefix("event: ")
            if event_type == "progress":
                data = json.loads(event.split("data: ", 1)[1])
                last_completed_node = data.get("node")
                logger.info(
                    "analysis_stream_progress request_id=%s last_completed_node=%s elapsed_ms=%s",
                    request_id,
                    last_completed_node,
                    int((time.monotonic() - started) * 1000),
                )
            elif event_type in {"complete", "error"}:
                outcome = event_type
                if event_type == "complete":
                    data = json.loads(event.split("data: ", 1)[1])
                    logger.info(
                        "analysis_stream_result request_id=%s response_id=%s commit_sha=%s "
                        "build_status=%s workflow_version=%s",
                        request_id,
                        data.get("response_id"),
                        data.get("commit_sha"),
                        data.get("build_status"),
                        data.get("workflow_version"),
                    )
            yield event
            if event_type in {"complete", "error"}:
                return
    except asyncio.TimeoutError:
        outcome = "timeout"
        detail = {
            "code": "analysis_timeout",
            "message": "Analysis exceeded the stream deadline. Do not automatically resubmit; "
            "an in-flight blocking operation or remote build may still be running.",
            "request_id": request_id,
            "workflow_version": workflow_version,
            "timeout_seconds": timeout_seconds,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "last_completed_node": last_completed_node,
            "retryable": False,
        }
        yield f"event: error\ndata: {json.dumps({'detail': detail})}\n\n"
    except Exception as exc:
        outcome = "error"
        logger.warning(
            "analysis_stream_error request_id=%s exception_type=%s",
            request_id,
            type(exc).__name__,
        )
        detail = {
            "code": "analysis_error",
            "request_id": request_id,
            "message": "Analysis failed. Use the request ID to locate server telemetry.",
        }
        yield f"event: error\ndata: {json.dumps({'detail': detail})}\n\n"
    finally:
        await events.aclose()
        logger.info(
            "analysis_stream_finished request_id=%s outcome=%s last_completed_node=%s elapsed_ms=%s",
            request_id,
            outcome,
            last_completed_node,
            int((time.monotonic() - started) * 1000),
        )
