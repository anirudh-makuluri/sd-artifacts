# Feedback Remediation Workflow

The feedback pipeline runs when a previous analysis exists for the same `repo_url + commit_sha` and user feedback requests improvements.

## When to Use It

Use `POST /feedback` or `POST /feedback/stream` when:
- Generated Dockerfile/compose/nginx needs targeted fixes.
- You want to keep the same commit context and iterate quickly.

## Execution Flow

1. Coordinator agent reads:
- User feedback text.
- Prior hadolint warnings.
- Prior risk notes.

2. Coordinator emits a per-artifact plan:
- `should_change` for Dockerfile, compose, and nginx.
- Action notes for each selected artifact.

3. Artifact improver agents apply targeted updates:
- Dockerfile improver updates only marked services.
- Compose improver updates `docker-compose.yml` only when required.
- Nginx improver updates `nginx.conf` only when required.

4. Verifier agent re-runs checks:
- Hadolint analysis.
- Risk review.
- Confidence scoring.

5. Updated artifacts are returned and upserted in cache for the same key.

## Failure Handling

- All nodes use the shared retry wrapper with exponential backoff + jitter.
- Structured output failures are retried.
- Node-level timeout budgets prevent indefinite execution.
- If coordinator planning fails, fallback marks all artifacts as changeable so remediation can still proceed.

## Inputs and Outputs

Input fields:
- `repo_url`
- `commit_sha`
- `feedback`

Output fields:
- Updated artifacts (`dockerfiles`, `docker_compose`, `nginx_conf`)
- `risks`
- `confidence`
- `hadolint_results`
- Other standard analysis metadata

## Practical Guidance

- Keep feedback concrete and deployment-specific.
- Mention failing paths, routes, ports, health checks, and service names.
- Use streaming mode when integrating with dashboards or CI logs.
