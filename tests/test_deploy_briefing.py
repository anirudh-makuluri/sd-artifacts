import pytest

from graph.nodes.deploy_briefing import (
    _build_briefing_prompt,
    _parse_llm_briefing,
    build_deterministic_briefing,
    deploy_briefing_node,
    validate_briefing_markdown,
)


def _static_state(start_command=None):
    deploy = {"startCommand": start_command} if start_command else {}
    return {"deploy_shape": "static_build", "deploy_units": [{
        "name": "web", "root": "apps/web", "type": "static_build", "framework": "vite",
        "port": 3000,  # Legacy metadata must not become runtime advice.
        "artifacts": {"railpack_plan": {"steps": [], "deploy": deploy}},
    }]}


def test_static_briefing_prompt_discards_legacy_port_hint():
    prompt = _build_briefing_prompt(_static_state())
    assert "3000" not in prompt
    assert '"port": null' in prompt
    assert "Development-server and preview ports are not production" in prompt


def test_deterministic_static_briefing_does_not_prescribe_an_application_port():
    briefing = build_deterministic_briefing(_static_state())
    assert "3000" not in briefing
    assert "static host, CDN" in briefing
    assert "plan specifies no serving command" in briefing
    assert "Expose the port" not in briefing


def test_static_plan_serving_command_is_preserved_without_guessing_a_port():
    briefing = build_deterministic_briefing(_static_state("caddy file-server --listen :8080"))
    assert "caddy file-server --listen :8080" in briefing
    assert "3000" not in briefing
    assert "plan specifies no serving command" not in briefing


def test_mixed_briefing_labels_server_port_as_a_default():
    state = _static_state()
    state["deploy_units"].append({"name": "api", "type": "server", "port": 8080})
    briefing = build_deterministic_briefing(state)
    assert "3000" not in briefing
    assert "classifier suggests port `8080` as a default" in briefing
    assert "confirm the application's actual listener" in briefing


def test_llm_networking_section_is_grounded_in_plan_metadata(monkeypatch):
    raw = "# Deploy briefing\n\n## Overview\n\nStatic app.\n\n## Build & run\n\nBuild assets.\n\n## Ports & networking\n\nExpose port 3000.\n\n## Environment variables\n\nConfigure build variables.\n\n## Risks & caveats\n\nVerify the build."
    monkeypatch.setattr("graph.nodes.deploy_briefing.invoke_with_retry", lambda **kwargs: (raw, 1, False))
    out = deploy_briefing_node(_static_state())
    assert "3000" not in out["deploy_briefing"]
    assert "3000" not in out["llm_outputs"]["briefing"]["preview"]
    assert "static host, CDN" in out["deploy_briefing"]
    assert "## Environment variables" in out["deploy_briefing"]
    assert out["llm_outputs"]["briefing"]["source"] == "llm"


@pytest.mark.parametrize("wrapper", [None, "markdown", ""])
def test_parser_preserves_complete_document_with_internal_command_fences(wrapper):
    document = (
        "# Deploy briefing\n\n## Overview\n\nStatic site.\n\n## Build & run\n\n"
        "```\npnpm install --frozen-lockfile --prefer-offline\n```\n\n"
        "```sh\npnpm --filter @capora/web run build\n```\n\n"
        "## Ports & networking\n\nUse static hosting.\n\n"
        "## Environment variables\n\nSet build variables.\n\n"
        "## Risks & caveats\n\nBuild verification was skipped."
    )
    raw = document if wrapper is None else f"```{wrapper}\n{document}\n```"
    assert _parse_llm_briefing(raw) == document


def test_parser_removes_preamble_without_extracting_internal_code():
    document = "# Deploy briefing\n\n## Overview\n\nA deployment document with build instructions.\n\n## Build & run\n\n```\npnpm run build\n```\n\nKeep the rest of the document."
    assert _parse_llm_briefing("Here is the briefing:\n```markdown\n" + document + "\n```") == document


def test_incomplete_llm_briefing_uses_complete_plan_based_fallback(monkeypatch):
    from services.briefing_policy import is_complete_briefing

    state = _static_state()
    state["deploy_units"][0]["artifacts"]["railpack_plan"]["steps"] = [{
        "name": "build", "commands": [{"cmd": "pnpm --filter @capora/web run build"}],
    }]
    monkeypatch.setattr("graph.nodes.deploy_briefing.invoke_with_retry", lambda **kwargs: (
        "# Deploy briefing\n\npnpm install --frozen-lockfile", 1, False,
    ))
    out = deploy_briefing_node(state)
    assert is_complete_briefing(out["deploy_briefing"])
    assert "pnpm --filter @capora/web run build" in out["deploy_briefing"]
    assert out["llm_outputs"]["briefing"] == {"source": "deterministic"}
    assert not out.get("errors")


def test_deploy_briefing_unpacks_invoke_with_retry_tuple(monkeypatch):
    def fake_invoke_with_retry(**kwargs):
        return ("# Deploy briefing\n\n## Overview\n\nWorks.", 1, False)

    monkeypatch.setattr("graph.nodes.deploy_briefing.invoke_with_retry", fake_invoke_with_retry)

    state = {
        "deploy_units": [
            {
                "name": "web",
                "root": "apps/web",
                "type": "static_build",
                "provider": "node",
                "framework": "next",
                "port": 3000,
                "artifacts": {"railpack_plan": {"steps": []}},
            }
        ],
        "deploy_shape": "static_build",
        "package_path": "apps/web",
        "commit_sha": "abc",
        "railpack_version": "0.26.1",
        "build_status": "passed",
    }

    out = deploy_briefing_node(state)

    assert out["deploy_briefing"].startswith("# Deploy briefing")
    assert "## Overview" in out["deploy_briefing"]
    assert not out["deploy_briefing"].startswith("(")


def test_validate_briefing_rejects_env_dump():
    with pytest.raises(ValueError, match="environment variable dump"):
        validate_briefing_markdown("CI=true\nNODE_ENV=production\nPNPM_HOME=/opt/pnpm")


def test_validate_briefing_rejects_single_command():
    with pytest.raises(ValueError, match="bare shell command"):
        validate_briefing_markdown('go build -ldflags="-w -s" -o out ./cmd/server')


def test_parse_llm_briefing_rejects_invalid_content():
    with pytest.raises(ValueError):
        _parse_llm_briefing(type("R", (), {"content": "pnpm install"})())


def test_build_deterministic_briefing_from_go_plan():
    state = {
        "deploy_shape": "server",
        "package_path": "apps/backend",
        "commit_sha": "abc123",
        "build_status": "passed",
        "railpack_version": "0.26.1",
        "deploy_units": [
            {
                "name": "backend",
                "root": "apps/backend",
                "type": "server",
                "provider": "go",
                "port": 8080,
                "artifacts": {
                    "railpack_plan": {
                        "steps": [
                            {
                                "name": "install",
                                "commands": [{"cmd": "go mod download"}],
                            },
                            {
                                "name": "build",
                                "commands": [{"cmd": 'go build -ldflags="-w -s" -o out ./cmd/server'}],
                            },
                        ],
                        "deploy": {
                            "startCommand": "./out",
                            "variables": {"CI": "true"},
                        },
                    }
                },
            }
        ],
    }

    briefing = build_deterministic_briefing(state)

    assert briefing.startswith("# Deploy briefing")
    assert "## Build & run" in briefing
    assert "go mod download" in briefing
    assert "./out" in briefing
    assert "`CI=true`" in briefing


def test_deploy_briefing_falls_back_when_llm_returns_garbage(monkeypatch):
    def fake_invoke_with_retry(**kwargs):
        raise RuntimeError("validation failed")

    monkeypatch.setattr("graph.nodes.deploy_briefing.invoke_with_retry", fake_invoke_with_retry)

    state = {
        "deploy_units": [
            {
                "name": "backend",
                "root": "apps/backend",
                "type": "server",
                "provider": "go",
                "port": 8080,
                "artifacts": {
                    "railpack_plan": {
                        "steps": [{"name": "build", "commands": [{"cmd": "go build -o out ."}]}],
                        "deploy": {"startCommand": "./out"},
                    }
                },
            }
        ],
        "deploy_shape": "server",
        "package_path": "apps/backend",
        "commit_sha": "abc",
        "build_status": "passed",
    }

    out = deploy_briefing_node(state)

    assert out["deploy_briefing"].startswith("# Deploy briefing")
    assert "## Build & run" in out["deploy_briefing"]
    assert "go build" in out["deploy_briefing"]
    assert out["llm_outputs"]["briefing"]["source"] == "deterministic"
