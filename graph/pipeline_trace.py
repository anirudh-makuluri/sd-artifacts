from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Dict, Generator, Optional


def append_trace(
    state: Dict[str, Any],
    node: str,
    status: str,
    *,
    duration_ms: int = 0,
    error: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Append a pipeline trace entry to state."""
    entry = {
        "node": node,
        "status": status,
        "duration_ms": duration_ms,
        "error": error,
        "meta": meta or {},
    }
    trace = state.setdefault("pipeline_trace", [])
    if isinstance(trace, list):
        trace.append(entry)


@contextmanager
def trace_node(state: Dict[str, Any], node: str) -> Generator[None, None, None]:
    """Context manager that records node start/success/error in pipeline_trace."""
    started = time.monotonic()
    try:
        yield
    except Exception as exc:
        append_trace(
            state,
            node,
            "error",
            duration_ms=int((time.monotonic() - started) * 1000),
            error=str(exc),
        )
        raise
    else:
        append_trace(
            state,
            node,
            "ok",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
