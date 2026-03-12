# API Examples

This document contains request/response examples for all public endpoints.

## Base URL

- Local: `http://localhost:8080`

## Analyze Repository

Endpoint: `POST /analyze`

Purpose:
- Runs scanner -> planner -> generators -> verifier and returns generated artifacts.

Example:
```bash
curl -X POST http://localhost:8080/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/user/repo-name",
    "max_files": 50
  }'
```

Notes:
- `github_token` is optional for public repos.
- `github_token` is required for private repos.

## Analyze Repository (Streaming)

Endpoint: `POST /analyze/stream`

Purpose:
- Runs the same analysis pipeline and emits Server-Sent Events (SSE) progress updates.

Example:
```bash
curl -N -X POST http://localhost:8080/analyze/stream \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/user/repo-name"
  }'
```

SSE event shape:
```text
event: progress
data: {"node": "scanner", "status": "completed"}

event: progress
data: {"node": "planner", "status": "completed"}

event: complete
data: { ... full JSON response ... }
```

## Feedback Remediation

Endpoint: `POST /feedback`

Purpose:
- Improves previously generated artifacts for the same `repo_url + commit_sha`.

Example:
```bash
curl -X POST http://localhost:8080/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/user/repo-name",
    "commit_sha": "abc123def456",
    "feedback": "The API container fails health checks and nginx is not routing /api correctly"
  }'
```

Notes:
- Cache row for `repo_url + commit_sha` must already exist.
- Response shape matches `/analyze`.
- Improved output is upserted into cache.

## Feedback Remediation (Streaming)

Endpoint: `POST /feedback/stream`

Purpose:
- Returns real-time remediation progress events via SSE.

Example:
```bash
curl -N -X POST http://localhost:8080/feedback/stream \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/user/repo-name",
    "commit_sha": "abc123def456",
    "feedback": "The API container fails health checks and nginx is not routing /api correctly"
  }'
```

SSE event shape:
```text
event: progress
data: {"node": "feedback_coordinator", "status": "completed"}

event: progress
data: {"node": "dockerfile_improver", "status": "completed"}

event: complete
data: { ... full JSON response ... }
```

## Seed Example Bank

Endpoint: `POST /examples/seed`

Purpose:
- Seeds Supabase example bank from an explicit list of repos.

Example:
```bash
curl -X POST http://localhost:8080/examples/seed \
  -H "Content-Type: application/json" \
  -d '{
    "repo_urls": [
      "https://github.com/vercel/next.js",
      "https://github.com/tiangolo/full-stack-fastapi-template"
    ],
    "max_files_per_repo": 20,
    "permissive_only": true
  }'
```

## Seed Popular Example Bank

Endpoint: `POST /examples/seed/popular`

Purpose:
- Seeds from the built-in popular repositories list.

Example:
```bash
curl -X POST "http://localhost:8080/examples/seed/popular"
```

## Preview Retrieved Examples

Endpoint: `POST /examples/preview`

Purpose:
- Shows the examples that will be injected into generation prompts.

Example:
```bash
curl -X POST http://localhost:8080/examples/preview \
  -H "Content-Type: application/json" \
  -d '{
    "artifact_type": "dockerfile",
    "detected_stack": "Next.js app with Node backend",
    "stack_tokens": ["node", "next", "react"],
    "service": {"name": "web", "build_context": "."},
    "limit": 3
  }'
```

## Delete Cached Analysis

Endpoint: `DELETE /cache`

Purpose:
- Deletes one cache entry or all entries for a repo.

Example:
```bash
curl -X DELETE http://localhost:8080/cache \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/user/repo-name",
    "commit_sha": "abc123def456"
  }'
```

Behavior:
- Include `commit_sha` to delete a specific row.
- Omit `commit_sha` to delete all rows for `repo_url`.

## Response Contract Summary

Representative response fields:
- `commit_sha`
- `stack_summary`
- `stack_tokens`
- `services`
- `dockerfiles`
- `docker_compose`
- `nginx_conf`
- `has_existing_dockerfiles`
- `has_existing_compose`
- `risks`
- `confidence`
- `hadolint_results`
- `token_usage`
