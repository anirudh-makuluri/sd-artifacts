from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, Iterable, Tuple

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from db import supabase
from tools.benchmark_storage import save_benchmark_artifact


def _iter_benchmark_json_files(benchmarks_dir: str) -> Iterable[str]:
    for name in sorted(os.listdir(benchmarks_dir)):
        if not name.lower().endswith(".json"):
            continue
        yield os.path.join(benchmarks_dir, name)


def _load_json(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def run(benchmarks_dir: str) -> Tuple[int, int]:
    imported = 0
    skipped = 0

    for path in _iter_benchmark_json_files(benchmarks_dir):
        try:
            payload = _load_json(path)
            if not isinstance(payload, dict):
                skipped += 1
                print(f"Skipping {os.path.basename(path)}: JSON root is not an object")
                continue
            save_benchmark_artifact(os.path.basename(path), payload)
            imported += 1
            print(f"Imported {os.path.basename(path)}")
        except Exception as exc:
            skipped += 1
            print(f"Skipping {os.path.basename(path)}: {exc}")

    return imported, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="Import benchmark JSON files into Supabase")
    parser.add_argument("--benchmarks-dir", default="benchmarks")
    args = parser.parse_args()

    if not supabase:
        print("Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")
        return 1

    benchmarks_dir = os.path.abspath(args.benchmarks_dir)
    if not os.path.isdir(benchmarks_dir):
        print(f"Benchmarks directory not found: {benchmarks_dir}")
        return 1

    imported, skipped = run(benchmarks_dir)
    print(f"Done. Imported={imported}, Skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
