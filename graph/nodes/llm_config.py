from langchain_aws import ChatBedrock
import os
import re
from dotenv import load_dotenv
from graph.llm_retry import RetryConfig
from langchain_core.callbacks import BaseCallbackHandler

load_dotenv()

BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")

_LLM_TEMPERATURES = {
    "llm_briefing": 0.2,
    "llm_repair": 0.1,
}


def __getattr__(name: str):
    if name in _LLM_TEMPERATURES:
        instance = ChatBedrock(
            model_id=BEDROCK_MODEL_ID,
            model_kwargs={"temperature": _LLM_TEMPERATURES[name], "max_tokens": 4096},
        )
        globals()[name] = instance
        return instance
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


RETRY_CONFIGS = {
    "briefing": RetryConfig(max_attempts=3, timeout_seconds=90.0, fallback_after_attempt=2),
    "repair": RetryConfig(max_attempts=3, timeout_seconds=90.0, fallback_after_attempt=2),
}


FALLBACK_PROMPTS = {
    "briefing": """
Write a markdown deploy briefing for smart-deploy operators.

Required headings:
# Deploy briefing
## Overview
## Build & run
## Ports & networking
## Environment variables
## Risks & caveats

Use bullet lists. Do not output only env vars or a single shell command.
""".strip(),
    "repair": """
Return ONLY raw JSON:
{
  "diagnosis": "string",
  "should_retry": boolean,
  "railpack_json": object or null,
  "env_overrides": {},
  "give_up_reason": "string or null"
}

Patch only railpack.json overrides or RAILPACK_* env vars.
""".strip(),
}


class TokenTracker(BaseCallbackHandler):
    """Callback handler that tracks token usage across all LLM calls."""

    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0

    def on_llm_end(self, response, **kwargs):
        usage = (response.llm_output or {}).get("usage", {})
        self.input_tokens += usage.get("prompt_tokens", 0)
        self.output_tokens += usage.get("completion_tokens", 0)
        self.total_tokens += usage.get("total_tokens", 0)

    def get_usage(self):
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens or (self.input_tokens + self.output_tokens),
        }


def strip_markdown_wrapper(content: str, lang: str = "docker") -> str:
    """Strip markdown code block wrappers and LLM preamble from output."""
    content = content.strip()

    code_block_pattern = rf"```(?:{lang}|dockerfile|yaml|nginx|conf|markdown|md)?\s*\n(.*?)```"
    match = re.search(code_block_pattern, content, re.DOTALL | re.IGNORECASE)
    if match:
        content = match.group(1).strip()
        return content

    content = content.strip("`").strip()
    if content.startswith(f"{lang}\n"):
        content = content[len(lang) + 1:]

    preamble_pattern = r"^(?:IMPROVED|REVIEWED|GENERATED|UPDATED|HERE(?:'S| IS))[\s\S]*?:\s*\n+"
    content = re.sub(preamble_pattern, "", content, count=1, flags=re.IGNORECASE)

    return content.strip()
