import pytest

from graph.nodes.deploy_briefing import (
    _parse_llm_briefing,
    build_deterministic_briefing,
    deploy_briefing_node,
    validate_briefing_markdown,
)


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
