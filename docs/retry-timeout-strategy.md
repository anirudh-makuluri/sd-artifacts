# Retry and Timeout Strategy

This project uses a shared retry wrapper for robustness around LLM and parsing failures.

## Nodes Covered

The following v2 nodes run through shared retry logic:

- `deploy_briefing` — markdown deploy briefing generation
- `railpack_build_repair` — AI diagnosis and patch when Railpack build fails

## Retry Behavior

- Exponential backoff is used for transient failures.
- Jitter is added to reduce synchronized retries.
- Structured output parsing failures are retried.
- JSON validation failures are retried.

## Timeout Budgets

- LLM attempts have node budgets; scanning, cloning, and other work add to total latency.
- A build or LLM timeout alone does not bound total request duration.
- Railpack build verification timeout is controlled via `SD_RAILPACK_VERIFY_TIMEOUT_SECONDS` (default `60` seconds locally and for remote CodeBuild waiting).

## Stream deadline and cancellation

`POST /analyze/stream` applies one absolute deadline across cache lookup, graph
execution, cache writes, and response logging. It defaults to 110 seconds and can
be set with `SD_ANALYZE_STREAM_TIMEOUT_SECONDS` (positive finite seconds; invalid
values use the default). Progress events do not restart the deadline. This setting
does not change `/analyze` or `/feedback` timeouts.

Cache hits have no intentional delay. Cold analysis has no guaranteed completion
time: a briefing alone has a 90-second budget, remote build waiting defaults to
60 seconds, and scanning, cloning, preparation, retries and persistence add time.
Increasing only the build timeout cannot make a cold run fit a 120-second client
deadline. Configure each client's total deadline and proxy limits above the
service deadline, allowing for queue, connection, and transport overhead.

The service returns `X-Request-ID` with the stream headers. On timeout it emits:

```text
event: error
data: {"detail":{"code":"analysis_timeout","message":"...","request_id":"...","timeout_seconds":110.0,"elapsed_ms":110000,"last_completed_node":"deploy_briefing","retryable":false}}
```

`last_completed_node` identifies the last emitted graph update, not a proven
currently running stage. Timeout errors do not set a build status or cache a
fabricated analysis. Completed analyses retain their actual `passed`, `failed`,
`skipped`, or other schema-v2 build status.

On timeout or client disconnect, the service cancels the stream's pending async
work and stops advancing the graph. Cancellation is best effort: Python cannot
interrupt a synchronous graph node or database operation already running in a
worker thread. That operation may finish later, including writing a result. A
remote CodeBuild job already submitted may continue independently; stream
cancellation does not call `StopBuild`. No automatic retry or reconnect is made.
Request IDs correlate telemetry; they are not durable job IDs, and reconnecting
with another POST starts a new request which can duplicate work. Do not blindly
retry a timed-out or disconnected request. Operators should correlate its logs
and any remote build before deciding whether to submit again. This endpoint does
not offer resumable job/status retrieval.

Logs under the `sd_artifacts.stream` logger include request start, completed-node
timings, terminal outcome, elapsed time, and terminal response/commit/build/workflow
metadata. Configure the server's logging to retain this logger at INFO level.
Request bodies, GitHub tokens, and raw pipeline outputs are not logged by this
instrumentation. Use `SD_WORKFLOW_VERSION` (or `GIT_SHA`) to identify deployments.

### Investigation and verification for issue #6

Code inspection found the missing total stream deadline and artificial 4–10
second cache delays. The prior 60-second build cap only covers build waiting.
These findings do not establish the stage or deployed revision responsible for
the 2026-10-23 11:37–11:40 UTC incident; historical hosted logs are needed for that.

Regression tests cover stalled graph execution, blocking storage, immediate
cache responses, cancellation without automatic retry, and terminal build
statuses. They use controlled local substitutes, not a hosted Capora invocation.
After deploying, correlate a single request for
`anirudh-makuluri/capora`, `package_path: "apps/web"`, `max_files: 20` with its
request ID, revision, actual analyzed commit, per-stage timings and terminal
event. Compare a cold run with a cache hit, and record the actual build status.
A health response or accepted request is not evidence of completed analysis.

## Prompt Fallback

- After repeated failures, nodes switch to fallback prompts.
- Fallback prompts prioritize output validity and minimal required fields.

## Configuration Location

Current defaults are defined in:

- `graph/nodes/llm_config.py`

Primary settings:

- `RETRY_CONFIGS`
- `FALLBACK_PROMPTS`

## Why This Matters

- Reduces flaky runs from transient model/network errors.
- Keeps pipeline behavior predictable under malformed responses.
- Improves end-to-end reliability for both `/analyze` and `/feedback` workflows.
