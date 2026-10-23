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

- Each node has a maximum execution time budget.
- Time budgets protect API responsiveness and avoid hangs.
- Railpack build verification timeout is controlled via `SD_RAILPACK_VERIFY_TIMEOUT_SECONDS` (default `60` seconds locally and for remote CodeBuild waiting).

## Prompt Fallback

- After repeated failures, nodes switch to fallback prompts.
- Fallback prompts prioritize output validity and minimal required fields.

## Optional Bedrock configuration

Bedrock clients initialize inside the guarded LLM invocation, so missing AWS
configuration cannot prevent importing the API. Credential, region, profile,
model-access and provider-validation failures are treated as unavailable optional
AI configuration, without retrying the same setup failure. The response uses the
deterministic briefing, omits raw provider errors, and skips further AI calls for
that request. The request-scoped flag is not returned in the API response and
does not disable later requests.

If a build fails and AI repair is unavailable, the repair history records a
generic skip while the build retains its actual failed status and logs. CodeBuild,
S3 and other non-Bedrock failures retain their normal behavior. Throttling,
timeouts, malformed model output and unexpected failures keep their existing
retry/diagnostic handling.

The configuration-error classification follows the documented
[Bedrock service errors](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_InvokeModel.html#API_runtime_InvokeModel_Errors)
and [botocore credential/configuration exceptions](https://docs.aws.amazon.com/boto3/latest/guide/error-handling.html).

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
