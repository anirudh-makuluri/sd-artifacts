from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from graph.llm_retry import invoke_with_retry
from graph.nodes.llm_config import (
    FALLBACK_PROMPTS,
    RETRY_CONFIGS,
    llm_briefing,
    strip_markdown_wrapper,
)
from graph.pipeline_trace import append_trace, trace_node


def _extract_plan_commands(plan: Optional[Dict[str, Any]]) -> Tuple[Optional[str], List[Tuple[str, str]]]:
    """Return (start_command, [(step_name, cmd), ...]) from a railpack plan."""
    if not isinstance(plan, dict):
        return None, []

    deploy = plan.get("deploy") if isinstance(plan.get("deploy"), dict) else {}
    start_command = deploy.get("startCommand") if isinstance(deploy.get("startCommand"), str) else None

    commands: List[Tuple[str, str]] = []
    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        step_name = str(step.get("name") or "step")
        for entry in step.get("commands") or []:
            if isinstance(entry, dict):
                cmd = entry.get("cmd")
                if isinstance(cmd, str) and cmd.strip():
                    commands.append((step_name, cmd.strip()))
    return start_command, commands


def _plan_variables(plan: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not isinstance(plan, dict):
        return {}
    deploy = plan.get("deploy")
    if not isinstance(deploy, dict):
        return {}
    variables = deploy.get("variables")
    if not isinstance(variables, dict):
        return {}
    return {str(k): str(v) for k, v in variables.items()}


def _summarize_unit_for_prompt(unit: Dict[str, Any]) -> Dict[str, Any]:
    artifacts = unit.get("artifacts", {}) if isinstance(unit.get("artifacts"), dict) else {}
    plan = artifacts.get("railpack_plan")
    start_command, commands = _extract_plan_commands(plan)
    return {
        "name": unit.get("name"),
        "root": unit.get("root"),
        "type": unit.get("type"),
        "provider": unit.get("provider"),
        "framework": unit.get("framework"),
        "port": unit.get("port"),
        "railpack_target": unit.get("railpack_target"),
        "start_command": start_command,
        "commands": [{"step": step, "cmd": cmd} for step, cmd in commands[:12]],
        "variables": _plan_variables(plan),
    }


def _looks_like_env_dump(text: str) -> bool:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return True
    env_like = sum(1 for line in lines if re.match(r"^[A-Z][A-Z0-9_]*=", line))
    return env_like >= max(1, int(len(lines) * 0.7))


def _looks_like_command_only(text: str) -> bool:
    stripped = text.strip()
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if "#" in stripped or "##" in stripped:
        return False
    if len(stripped) >= 200 or len(lines) > 2:
        return False
    command_markers = (
        "go build",
        "npm run",
        "pnpm ",
        "yarn ",
        "node ",
        "python ",
        "uvicorn",
        "docker ",
        "./out",
    )
    return any(marker in stripped for marker in command_markers)


def validate_briefing_markdown(text: str) -> str:
    """Validate LLM briefing output; raise ValueError when it is not usable markdown."""
    content = (text or "").strip()
    if _looks_like_env_dump(content):
        raise ValueError("briefing looks like an environment variable dump")
    if _looks_like_command_only(content):
        raise ValueError("briefing looks like a bare shell command")
    if len(content) < 120:
        raise ValueError("briefing too short")
    if not content.startswith("#") and "##" not in content[:400]:
        raise ValueError("briefing missing markdown heading")
    return content


def _parse_llm_briefing(raw: Any) -> str:
    content = raw.content if hasattr(raw, "content") else str(raw)
    content = content.strip()

    fenced_md = re.search(r"```(?:markdown|md)\s*\n(.*?)```", content, re.DOTALL | re.IGNORECASE)
    if fenced_md:
        content = fenced_md.group(1).strip()
    elif "```" in content and "#" in content:
        content = strip_markdown_wrapper(content, lang="markdown")
    elif not content.startswith("#"):
        content = strip_markdown_wrapper(content, lang="markdown")

    if not content.startswith("#"):
        content = f"# Deploy briefing\n\n{content}"
    return validate_briefing_markdown(content)


def _build_briefing_prompt(state: Dict[str, Any]) -> str:
    units: List[Dict[str, Any]] = state.get("deploy_units") or []
    summaries = [_summarize_unit_for_prompt(unit) for unit in units if isinstance(unit, dict)]

    return f"""You write deploy briefings for smart-deploy.xyz operators.

Write a complete markdown document for humans (not JSON, not shell-only output).

Required sections (use these exact headings):
# Deploy briefing
## Overview
## Build & run
## Ports & networking
## Environment variables
## Risks & caveats

Rules:
- Explain install, build, and start using the railpack plan commands provided.
- Mention each deploy unit by name and path.
- List important env vars the app or Railpack may need in production.
- Do NOT output only env vars or a single command.
- Do NOT wrap the whole briefing in a code fence.

Context:
deploy_shape: {state.get("deploy_shape")}
package_path: {state.get("package_path", ".")}
commit_sha: {state.get("commit_sha", "unknown")}
railpack_version: {state.get("railpack_version")}
build_status: {state.get("build_status", "unknown")}

deploy_units:
{json.dumps(summaries, indent=2)[:10000]}
"""


def build_deterministic_briefing(state: Dict[str, Any]) -> str:
    """Synthesize a markdown briefing from classified units and railpack plans."""
    units: List[Dict[str, Any]] = [
        unit for unit in (state.get("deploy_units") or []) if isinstance(unit, dict)
    ]
    package_path = state.get("package_path", ".")
    commit_sha = state.get("commit_sha", "unknown")
    deploy_shape = state.get("deploy_shape", "unknown")
    build_status = state.get("build_status", "unknown")
    railpack_version = state.get("railpack_version") or "unknown"

    lines = [
        "# Deploy briefing",
        "",
        "## Overview",
        "",
        f"This package deploys via **Railpack** as `{deploy_shape}` for scope `{package_path}` "
        f"at commit `{commit_sha}`.",
        f"Build verification on sd-artifacts: **{build_status}** (Railpack {railpack_version}).",
        "",
        "## Build & run",
        "",
    ]

    if not units:
        lines.append("No deploy units were classified.")
    else:
        for unit in units:
            name = unit.get("name", "app")
            root = unit.get("root", ".")
            unit_type = unit.get("type", "server")
            provider = unit.get("provider") or "unknown"
            framework = unit.get("framework")
            port = unit.get("port")
            artifacts = unit.get("artifacts") if isinstance(unit.get("artifacts"), dict) else {}
            plan = artifacts.get("railpack_plan")
            start_command, commands = _extract_plan_commands(plan)
            target = unit.get("railpack_target") if isinstance(unit.get("railpack_target"), dict) else {}

            lines.append(f"### {name} (`{root}`)")
            lines.append("")
            meta = f"- **Type:** {unit_type} ({provider}"
            if framework:
                meta += f", {framework}"
            meta += ")"
            lines.append(meta)
            if port is not None:
                lines.append(f"- **Port:** {port}")
            if target.get("is_workspace"):
                lines.append(
                    f"- **Monorepo:** builds from repo root with "
                    f"`{target.get('build_cmd') or 'workspace filter'}`"
                )
            lines.append("")

            if commands:
                lines.append("**Pipeline steps:**")
                for step_name, cmd in commands:
                    lines.append(f"- `{step_name}`: `{cmd}`")
                lines.append("")
            else:
                lines.append("Railpack plan commands were not available.")
                lines.append("")

            if start_command:
                lines.append(f"**Start command:** `{start_command}`")
                lines.append("")

    lines.extend(
        [
            "## Ports & networking",
            "",
            "Expose the port listed for each deploy unit. Configure your platform health checks "
            "against the app start command above.",
            "",
            "## Environment variables",
            "",
        ]
    )

    any_vars = False
    for unit in units:
        artifacts = unit.get("artifacts") if isinstance(unit.get("artifacts"), dict) else {}
        variables = _plan_variables(artifacts.get("railpack_plan"))
        if not variables:
            continue
        any_vars = True
        lines.append(f"**{unit.get('name', 'app')}** (Railpack plan defaults):")
        for key, value in sorted(variables.items()):
            lines.append(f"- `{key}={value}`")
        lines.append("")

    if not any_vars:
        lines.append("No Railpack default variables were captured. Add app secrets in your deploy platform.")
        lines.append("")

    lines.extend(
        [
            "## Risks & caveats",
            "",
            "- Confirm production secrets (database URLs, API keys) are set in smart-deploy before go-live.",
            "- Monorepo packages may require workspace-aware build commands; use the scoped `package_path`.",
            "- Re-run `/analyze` with `refresh: true` after changing dependencies or Railpack config.",
            "",
        ]
    )
    return "\n".join(lines).strip()


def deploy_briefing_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """LLM generates markdown deploy briefing from railpack plans."""
    with trace_node(state, "deploy_briefing"):
        if state.get("error"):
            return state

        units = state.get("deploy_units") or []
        if not units:
            state["deploy_briefing"] = "# Deploy briefing\n\nNo deploy units were classified."
            return state

        prompt = _build_briefing_prompt(state)
        briefing: Optional[str] = None
        llm_outputs = state.setdefault("llm_outputs", {})

        try:
            briefing, attempts, fallback_used = invoke_with_retry(
                invoke_fn=lambda p: llm_briefing.invoke(p),
                prompt=prompt,
                validator=_parse_llm_briefing,
                fallback_prompt=FALLBACK_PROMPTS.get("briefing"),
                config=RETRY_CONFIGS.get("briefing"),
                node_name="deploy_briefing",
            )
            llm_outputs["briefing"] = {
                "source": "llm",
                "attempts": attempts,
                "fallback_used": fallback_used,
                "preview": briefing[:8000],
            }
        except Exception as exc:
            state.setdefault("errors", []).append(f"deploy_briefing LLM failed: {exc}")
            llm_outputs["briefing"] = {"source": "deterministic", "llm_error": str(exc)}
            append_trace(state, "deploy_briefing", "fallback", error=str(exc))

        if not briefing or not briefing.strip():
            briefing = build_deterministic_briefing(state)
            llm_outputs.setdefault("briefing", {})["source"] = "deterministic"

        state["deploy_briefing"] = briefing.strip()
        return state
