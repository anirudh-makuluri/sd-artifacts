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
    monkeypatch.setattr("graph.nodes.railpack_build_repair.remote_builds_enabled", lambda: False)
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


def test_remote_builds_enabled_is_hardcoded():
    assert remote_builds.remote_builds_enabled() is True
    settings = remote_builds._settings()
    assert settings["region"] == "us-west-2"
    assert settings["project_name"] == "smartdeploy-builder"
    assert settings["source_bucket"] == "smart-deploy-codebuild-bucket"
    assert settings["wait_timeout_seconds"] == 900
    assert settings["poll_interval_seconds"] == 10
    assert settings["max_log_lines"] == 80


def test_remote_build_defaults_name_root_image_by_repo_and_short_commit(monkeypatch):
    monkeypatch.setattr(remote_builds, "_registry_uri", lambda: "123456789012.dkr.ecr.us-east-1.amazonaws.com")

    defaults = remote_builds._build_defaults(
        repo_url="https://github.com/acme/my-repo.git",
        commit_sha="abcdef1234567890",
        units=[{"name": "api", "root": "."}],
    )

    assert defaults["api"]["ecr_repository"] == "sd/my-repo/root"
    assert defaults["api"]["image_tag"] == "abcdef"
    assert defaults["api"]["image_uri"] == "123456789012.dkr.ecr.us-east-1.amazonaws.com/sd/my-repo/root:abcdef"
    assert defaults["api"]["project_name"] == "smartdeploy-builder"


def test_remote_build_defaults_include_package_path_for_nested_unit(monkeypatch):
    monkeypatch.setattr(remote_builds, "_registry_uri", lambda: "123456789012.dkr.ecr.us-east-1.amazonaws.com")

    defaults = remote_builds._build_defaults(
        repo_url="git@github.com:acme/my-repo.git",
        commit_sha="123456abcdef",
        units=[{"name": "web", "root": "apps/web"}],
    )

    assert defaults["web"]["ecr_repository"] == "sd/my-repo/apps/web"
    assert defaults["web"]["image_tag"] == "123456"
    assert defaults["web"]["image_uri"] == "123456789012.dkr.ecr.us-east-1.amazonaws.com/sd/my-repo/apps/web:123456"


def test_remote_build_s3_key_uses_fixed_repo_package_layout():
    key = remote_builds._s3_key(
        {
            "repo_url": "https://github.com/acme/my-repo.git",
            "package_path": "apps/web",
        },
        "source",
        "bundle.zip",
    )

    assert key == "sd/my-repo/apps/web/source/bundle.zip"


def test_remote_build_s3_key_uses_root_for_repo_root_package():
    key = remote_builds._s3_key(
        {
            "repo_url": "git@github.com:acme/my-repo.git",
            "package_path": ".",
        },
        "results",
        "bundle.json",
    )

    assert key == "sd/my-repo/root/results/bundle.json"


def test_remote_build_runner_ensures_ecr_repository_before_build():
    script = remote_builds._runner_script()

    assert "def ensure_repository" in script
    assert "aws', 'ecr', 'create-repository'" in script
    assert "ensure_repository(unit['repository_name'], region)" in script


def test_remote_build_buildspec_installs_railpack_when_missing():
    buildspec = remote_builds._buildspec()

    assert "if ! command -v railpack" in buildspec
    assert "https://railpack.com/install.sh" in buildspec
    assert "RAILPACK_VERSION=0.26.1" in buildspec
    assert "docker run -d --privileged --name buildkit" in buildspec
    assert "railpack --version" in buildspec


def test_remote_build_finalize_uses_result_map_when_codebuild_fails(monkeypatch):
    defaults = {
        "api": {
            "provider": "aws_codebuild",
            "backend": "railpack",
            "status": "PENDING",
            "unit_name": "api",
            "unit_root": "apps/api",
            "image_uri": "registry/sd/repo/apps/api:abcdef",
            "image_digest": None,
            "failure_reason": None,
        },
        "web": {
            "provider": "aws_codebuild",
            "backend": "railpack",
            "status": "PENDING",
            "unit_name": "web",
            "unit_root": "apps/web",
            "image_uri": "registry/sd/repo/apps/web:abcdef",
            "image_digest": None,
            "failure_reason": None,
        },
    }
    monkeypatch.setattr(
        remote_builds,
        "_read_result_map",
        lambda _uri: {
            "api": {
                "status": "SUCCEEDED",
                "image_uri": "registry/sd/repo/apps/api:abcdef",
                "image_digest": "sha256:api",
            },
            "web": {
                "status": "FAILED",
                "failure_reason": "Command failed (1): railpack build apps/web",
            },
        },
    )
    monkeypatch.setattr(remote_builds, "_log_excerpt", lambda _build: "build log tail")
    monkeypatch.setattr(remote_builds, "_codebuild_logs_url", lambda _build: "https://example.com/logs")

    finalized = remote_builds._finalize(
        defaults=defaults,
        build={"buildStatus": "FAILED", "id": "project:build"},
        result_s3_uri="s3://bucket/results.json",
    )

    assert finalized["api"]["status"] == "SUCCEEDED"
    assert finalized["api"]["image_digest"] == "sha256:api"
    assert finalized["web"]["status"] == "FAILED"
    assert finalized["web"]["failure_reason"] == "Command failed (1): railpack build apps/web"
    assert finalized["web"]["log_excerpt"] == "build log tail"
    assert remote_builds._aggregate_status(finalized.values()) == "partial"


def test_remote_build_wait_marks_client_timeout(monkeypatch):
    monkeypatch.setattr(
        remote_builds,
        "_get_build",
        lambda _build_id: {"buildStatus": "IN_PROGRESS", "id": "project:build"},
    )

    build = remote_builds._wait_for_build("project:build", timeout_seconds=0, poll_interval_seconds=1)

    assert build["buildStatus"] == "TIMED_OUT"
    assert "Timed out waiting for CodeBuild build" in build["sdFailureReason"]
