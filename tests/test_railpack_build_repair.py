from graph.nodes.railpack_build_repair import (
    _build_verification_skip_reason,
    railpack_build_repair_node,
)
from tools import remote_builds


def test_build_verification_skip_reason_defaults_on_render(monkeypatch):
    monkeypatch.delenv("SD_SKIP_RAILPACK_BUILD", raising=False)
    monkeypatch.setenv("RENDER", "true")

    assert _build_verification_skip_reason() == (
        "Render native runtime detected; BuildKit-backed railpack build is disabled"
    )


def test_build_verification_skip_reason_can_be_disabled_explicitly(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("SD_SKIP_RAILPACK_BUILD", "false")

    assert _build_verification_skip_reason() is None


def test_railpack_build_repair_skips_cleanly_on_render(monkeypatch, tmp_path):
    monkeypatch.delenv("SD_SKIP_RAILPACK_BUILD", raising=False)
    monkeypatch.setenv("RENDER", "true")

    state = {
        "repo_dir": str(tmp_path),
        "deploy_units": [
            {
                "name": "api",
                "root": ".",
                "type": "server",
                "framework": "fastapi",
                "port": 8000,
            }
        ],
        "repair_history": [],
        "pipeline_trace": [],
    }

    out = railpack_build_repair_node(state)

    assert out["build_status"] == "skipped"
    assert out["build_verification"]["status"] == "skipped"
    assert "Render native runtime detected" in out["build_verification"]["message"]
    assert out["repair_history"] == []
    assert any(
        entry.get("node") == "railpack_build_repair" and entry.get("status") == "skipped"
        for entry in out["pipeline_trace"]
        if isinstance(entry, dict)
    )


def test_railpack_build_repair_uses_remote_build_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setattr("graph.nodes.railpack_build_repair.remote_builds_enabled", lambda: True)

    def fake_remote(state):
        remote_build = {
            "provider": "aws_codebuild",
            "backend": "railpack",
            "status": "SUCCEEDED",
            "unit_name": "api",
            "unit_root": ".",
            "image_uri": "123456789012.dkr.ecr.us-east-1.amazonaws.com/sd/repo:sha-ap",
            "image_digest": "sha256:abc",
            "build_id": "project:build",
            "logs_url": "https://example.com/logs",
        }
        state["remote_builds"] = {"api": remote_build}
        state["deploy_units"][0]["remote_build"] = remote_build
        state["build_status"] = "passed"
        state["build_verification"] = {
            "backend": "aws_codebuild_railpack",
            "status": "passed",
            "message": "Remote build passed",
            "attempts": 1,
            "duration_seconds": 1.0,
            "log_excerpt": "",
        }
        return state

    monkeypatch.setattr("graph.nodes.railpack_build_repair.run_remote_railpack_builds", fake_remote)

    state = {
        "repo_dir": str(tmp_path),
        "deploy_units": [
            {
                "name": "api",
                "root": ".",
                "type": "server",
                "framework": "fastapi",
                "port": 8000,
            }
        ],
        "repair_history": [],
        "pipeline_trace": [],
    }

    out = railpack_build_repair_node(state)

    assert out["build_status"] == "passed"
    assert out["build_verification"]["backend"] == "aws_codebuild_railpack"
    assert out["remote_builds"]["api"]["image_digest"] == "sha256:abc"
    assert out["deploy_units"][0]["remote_build"]["image_uri"].endswith(":sha-ap")


def test_remote_build_defaults_name_root_image_by_repo_and_short_commit(monkeypatch):
    monkeypatch.setenv("SD_CODEBUILD_PROJECT_NAME", "project")
    monkeypatch.setenv("SD_CODEBUILD_ECR_REPOSITORY_PREFIX", "sd")
    monkeypatch.setattr(remote_builds, "_registry_uri", lambda: "123456789012.dkr.ecr.us-east-1.amazonaws.com")

    defaults = remote_builds._build_defaults(
        repo_url="https://github.com/acme/my-repo.git",
        commit_sha="abcdef1234567890",
        units=[{"name": "api", "root": "."}],
    )

    assert defaults["api"]["ecr_repository"] == "sd/my-repo"
    assert defaults["api"]["image_tag"] == "abcdef"
    assert defaults["api"]["image_uri"] == "123456789012.dkr.ecr.us-east-1.amazonaws.com/sd/my-repo:abcdef"


def test_remote_build_defaults_include_package_path_for_nested_unit(monkeypatch):
    monkeypatch.setenv("SD_CODEBUILD_PROJECT_NAME", "project")
    monkeypatch.setenv("SD_CODEBUILD_ECR_REPOSITORY_PREFIX", "sd")
    monkeypatch.setattr(remote_builds, "_registry_uri", lambda: "123456789012.dkr.ecr.us-east-1.amazonaws.com")

    defaults = remote_builds._build_defaults(
        repo_url="git@github.com:acme/my-repo.git",
        commit_sha="123456abcdef",
        units=[{"name": "web", "root": "apps/web"}],
    )

    assert defaults["web"]["ecr_repository"] == "sd/my-repo/apps/web"
    assert defaults["web"]["image_tag"] == "123456"
    assert defaults["web"]["image_uri"] == "123456789012.dkr.ecr.us-east-1.amazonaws.com/sd/my-repo/apps/web:123456"
