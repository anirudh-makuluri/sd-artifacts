from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from db import supabase
from tools.stack_tokens import LABEL_REQUIRED_TOKEN_ORDER, normalize_stack_token


def infer_service_name(package_path: str, tags: set[str]) -> str:
    lower_pkg = package_path.lower()
    if "backend" in lower_pkg or "fastapi" in tags:
        return "api"
    if "frontend" in lower_pkg or "nextjs" in tags or "nginx" in tags:
        return "web"
    return "app"


def infer_required_tokens(tags: set[str]) -> list[str]:
    normalized_tags = {normalize_stack_token(tag) for tag in tags}
    mapped = []
    for token in LABEL_REQUIRED_TOKEN_ORDER:
        if token not in normalized_tags:
            continue
        mapped.append(token)

    seen: set[str] = set()
    result: list[str] = []
    for item in mapped:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def choose_package_path(source_path: str) -> str:
    raw_pkg = "/".join(source_path.split("/")[:-1]) or "."
    if "/docker/" in raw_pkg:
        parent = raw_pkg.split("/docker/")[0]
        return parent or raw_pkg
    return raw_pkg


def exposed_ports(content: str) -> list[int]:
    return [int(value) for value in re.findall(r"(?im)^\s*EXPOSE\s+(\d+)", content or "")]


def most_common(values: list[int]) -> int | None:
    if not values:
        return None
    return max(set(values), key=values.count)


def main() -> int:
    if not supabase:
        print("Supabase is not configured; cannot generate labels.")
        return 1

    rows = (
        supabase.table("example_bank")
        .select("source_repo,source_path,artifact_type,stack_tags,content,is_active")
        .eq("is_active", True)
        .limit(2000)
        .execute()
        .data
        or []
    )

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("artifact_type") != "dockerfile":
            continue

        source_repo = (row.get("source_repo") or "").strip("/")
        source_path = row.get("source_path") or ""
        if not source_repo or not source_path:
            continue
        if source_path.startswith(".github/"):
            continue

        package_path = choose_package_path(source_path)
        grouped[(source_repo, package_path)].append(row)

    labels = []
    for (repo, package_path), items in sorted(grouped.items()):
        tagset: set[str] = set()
        ports: list[int] = []

        for item in items:
            for tag in item.get("stack_tags") or []:
                if isinstance(tag, str):
                    tagset.add(tag.lower())
            ports.extend(exposed_ports(item.get("content") or ""))

        service_name = infer_service_name(package_path, tagset)
        required_tokens = infer_required_tokens(tagset)
        expected_ports: dict[str, int] = {}

        preferred_port = most_common(ports)
        if preferred_port is not None:
            expected_ports[service_name] = preferred_port

        labels.append(
            {
                "repo": repo,
                "package_path": package_path,
                "required_stack_tokens": required_tokens,
                "expected_services": [
                    {
                        "name": service_name,
                        "build_context": package_path,
                    }
                ],
                "excluded_services": [],
                "expected_ports": expected_ports,
            }
        )

    output = {"repos": labels}
    os.makedirs("benchmarks", exist_ok=True)

    output_files = [
        os.path.join("benchmarks", "example_bank_labels.json"),
        os.path.join("benchmarks", "example_bank_labels.sample.json"),
    ]
    for output_file in output_files:
        with open(output_file, "w", encoding="utf-8") as handle:
            json.dump(output, handle, indent=2)

    print(f"Wrote {len(labels)} label entries")
    for label in labels:
        service = label["expected_services"][0]
        print(
            f"- {label['repo']} | package_path={label['package_path']} | "
            f"service={service['name']} | ports={label['expected_ports']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
