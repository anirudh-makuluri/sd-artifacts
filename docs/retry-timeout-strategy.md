# Retry and Timeout Strategy

This project uses a shared retry wrapper for robustness around LLM and parsing failures.

## Nodes Covered

The following nodes run through shared retry logic:
- Planner
- Dockerfile generator
- Compose generator
- Nginx generator
- Verifier
- Feedback coordinator
- Feedback remediation agents

## Retry Behavior

- Exponential backoff is used for transient failures.
- Jitter is added to reduce synchronized retries.
- Structured output parsing failures are retried.
- JSON validation failures are retried.

## Timeout Budgets

- Each node has a maximum execution time budget.
- Time budgets protect API responsiveness and avoid hangs.

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
