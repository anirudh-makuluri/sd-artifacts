from __future__ import annotations

from typing import Any, Dict, List, Optional
import re
import requests

from db import supabase


POPULAR_EXAMPLE_REPOS = [
    "https://github.com/vercel/next.js",
    "https://github.com/tiangolo/full-stack-fastapi-template",
    "https://github.com/django/django",
    "https://github.com/golang/example",
    "https://github.com/redis/redis",
    "https://github.com/strapi/strapi",
]


def _repo_full_name(repo_url: str) -> str:
    return repo_url.split("github.com/")[-1].strip().strip("/")


def _headers(github_token: Optional[str]) -> Dict[str, str]:
    if github_token:
        return {"Authorization": f"token {github_token}"}
    return {}


def _infer_artifact_type(path: str) -> Optional[str]:
    name = path.split("/")[-1].lower()
    if name == "dockerfile" or name.startswith("dockerfile.") or name.endswith(".dockerfile"):
        return "dockerfile"
    if name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        return "compose"
    return None


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9_\-.+]+", (text or "").lower())


def _infer_tags(path: str, content: str) -> List[str]:
    tags = set()
    haystack = f"{path}\n{content}".lower()

    # Runtime tags
    runtime_patterns = {
        "node": ["node:", "npm", "yarn", "pnpm", "next", "react"],
        "python": ["python:", "pip", "poetry", "fastapi", "django", "flask", "gunicorn", "uvicorn"],
        "go": ["golang:", "go build", "go mod", "go.sum"],
        "java": ["openjdk", "temurin", "maven", "gradle"],
        "dotnet": ["mcr.microsoft.com/dotnet", "dotnet publish"],
        "ruby": ["ruby:", "bundle install", "rails"],
        "php": ["php:", "composer", "laravel"],
    }

    framework_patterns = {
        "nextjs": ["next", "next start"],
        "nestjs": ["nestjs", "@nestjs"],
        "fastapi": ["fastapi", "uvicorn"],
        "django": ["django", "manage.py"],
        "flask": ["flask"],
        "express": ["express"],
        "redis": ["redis"],
        "postgres": ["postgres", "postgresql"],
        "nginx": ["nginx"],
    }

    for tag, markers in runtime_patterns.items():
        if any(marker in haystack for marker in markers):
            tags.add(tag)

    for tag, markers in framework_patterns.items():
        if any(marker in haystack for marker in markers):
            tags.add(tag)

    # Best-practice tags
    if " as builder" in haystack or "as build" in haystack:
        tags.add("multistage")
    if "healthcheck" in haystack:
        tags.add("healthcheck")
    if "user " in haystack:
        tags.add("nonroot")

    return sorted(tags)


def _quality_score(content: str) -> float:
    text = (content or "").lower()
    score = 0.45
    if "healthcheck" in text:
        score += 0.15
    if " as builder" in text or "as build" in text:
        score += 0.15
    if "user " in text:
        score += 0.15
    if "alpine" in text or "slim" in text:
        score += 0.05
    if len(content) > 180:
        score += 0.05
    return min(score, 1.0)


def _license(meta: Dict[str, Any]) -> Optional[str]:
    license_info = meta.get("license") or {}
    return license_info.get("spdx_id") if isinstance(license_info, dict) else None


def _permissive_license(spdx_id: Optional[str]) -> bool:
    return spdx_id in {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"}


def seed_example_bank_from_repos(
    repo_urls: List[str],
    github_token: Optional[str] = None,
    max_files_per_repo: int = 20,
    permissive_only: bool = True,
) -> Dict[str, Any]:
    """Scrape Dockerfile/compose examples from GitHub repos into Supabase example_bank."""
    if not supabase:
        return {
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "errors": ["Supabase client not configured."],
        }

    inserted = 0
    updated = 0
    skipped = 0
    errors: List[str] = []

    for repo_url in repo_urls:
        try:
            repo = _repo_full_name(repo_url)
            headers = _headers(github_token)

            meta_resp = requests.get(f"https://api.github.com/repos/{repo}", headers=headers, timeout=20)
            if meta_resp.status_code != 200:
                errors.append(f"{repo}: metadata request failed ({meta_resp.status_code})")
                continue

            meta = meta_resp.json()
            spdx_id = _license(meta)
            if permissive_only and not _permissive_license(spdx_id):
                skipped += 1
                continue

            branch = meta.get("default_branch", "main")
            tree_resp = requests.get(
                f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1",
                headers=headers,
                timeout=30,
            )
            if tree_resp.status_code != 200:
                errors.append(f"{repo}: tree request failed ({tree_resp.status_code})")
                continue

            tree = tree_resp.json().get("tree", [])
            candidates = []
            for item in tree:
                if item.get("type") != "blob":
                    continue
                path = item.get("path", "")
                artifact_type = _infer_artifact_type(path)
                if artifact_type:
                    candidates.append((path, artifact_type))
                if len(candidates) >= max_files_per_repo:
                    break

            for path, artifact_type in candidates:
                raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"
                raw_resp = requests.get(raw_url, headers=headers, timeout=20)
                if raw_resp.status_code != 200:
                    errors.append(f"{repo}/{path}: content request failed ({raw_resp.status_code})")
                    continue

                content = raw_resp.text[:30000]
                tags = _infer_tags(path, content)
                snippet = content[:3000]
                score = _quality_score(content)

                row = {
                    "source_repo": repo,
                    "source_path": path,
                    "artifact_type": artifact_type,
                    "stack_tags": tags,
                    "license": spdx_id,
                    "quality_score": score,
                    "snippet": snippet,
                    "content": content,
                    "is_active": True,
                }

                existing = (
                    supabase.table("example_bank")
                    .select("id")
                    .eq("source_repo", repo)
                    .eq("source_path", path)
                    .limit(1)
                    .execute()
                )

                if existing.data:
                    supabase.table("example_bank").update(row).eq("source_repo", repo).eq("source_path", path).execute()
                    updated += 1
                else:
                    supabase.table("example_bank").insert(row).execute()
                    inserted += 1

        except Exception as exc:
            errors.append(f"{repo_url}: {exc}")

    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }


def _query_tags(detected_stack: str, service: Optional[Dict[str, Any]]) -> List[str]:
    tokens = set(_tokenize(detected_stack))
    if service:
        tokens.update(_tokenize(service.get("name", "")))
        tokens.update(_tokenize(service.get("build_context", "")))
    stop = {
        "with",
        "and",
        "the",
        "app",
        "service",
        "server",
        "api",
        "web",
        "repo",
        "unknown",
    }
    return [t for t in tokens if len(t) > 2 and t not in stop]


def fetch_reference_examples(
    artifact_type: str,
    detected_stack: str,
    service: Optional[Dict[str, Any]] = None,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """Fetch and rank active examples from Supabase for prompt grounding."""
    if not supabase:
        return []

    try:
        response = (
            supabase.table("example_bank")
            .select("source_repo,source_path,artifact_type,stack_tags,quality_score,snippet")
            .eq("artifact_type", artifact_type)
            .eq("is_active", True)
            .order("quality_score", desc=True)
            .limit(50)
            .execute()
        )
        rows = response.data or []
    except Exception:
        return []

    query_tokens = set(_query_tags(detected_stack, service))
    ranked: List[tuple[float, Dict[str, Any]]] = []
    for row in rows:
        tags = set((row.get("stack_tags") or []))
        overlap = len(tags.intersection(query_tokens))
        quality = float(row.get("quality_score") or 0.0)
        score = quality + (overlap * 0.12)
        ranked.append((score, row))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in ranked[:limit]]


def format_examples_for_prompt(examples: List[Dict[str, Any]]) -> str:
    if not examples:
        return "No matching examples found in example_bank."

    blocks = []
    for idx, ex in enumerate(examples, start=1):
        snippet = (ex.get("snippet") or "").strip()
        blocks.append(
            "\n".join(
                [
                    f"Example {idx}",
                    f"Repo: {ex.get('source_repo')}",
                    f"Path: {ex.get('source_path')}",
                    f"Tags: {', '.join(ex.get('stack_tags') or [])}",
                    "Snippet:",
                    snippet,
                ]
            )
        )

    return "\n\n---\n\n".join(blocks)
