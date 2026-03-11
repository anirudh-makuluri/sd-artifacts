from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Optional

from db import supabase


def benchmark_artifact_type_from_file_name(file_name: str) -> str:
    name = (file_name or "").lower()
    if name.startswith("scan-quality-"):
        return "scan_quality_report"
    if name == "latest-scan-quality.json":
        return "scan_quality_latest"
    if "labels" in name:
        return "benchmark_labels"
    return "benchmark_json"


def _coerce_generated_at(payload: Dict[str, Any]) -> Optional[str]:
    value = payload.get("generated_at")
    if not value:
        return None
    if isinstance(value, str):
        try:
            # Accept both timezone-aware and timezone-naive ISO values.
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value
        except ValueError:
            return None
    return None


def save_benchmark_artifact(file_name: str, payload: Dict[str, Any]) -> None:
    if not supabase:
        return

    record = {
        "file_name": file_name,
        "artifact_type": benchmark_artifact_type_from_file_name(file_name),
        "run_id": payload.get("run_id"),
        "generated_at": _coerce_generated_at(payload),
        "payload": payload,
    }

    supabase.table("benchmark_artifacts").upsert(record, on_conflict="file_name").execute()


def save_benchmark_artifact_from_path(file_path: str, payload: Dict[str, Any]) -> None:
    file_name = os.path.basename(file_path)
    save_benchmark_artifact(file_name=file_name, payload=payload)
