import subprocess

import pytest

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


def test_railpack_build_repair_treats_timeout_as_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr("graph.nodes.railpack_build_repair.remote_builds_enabled", lambda: False)
    monkeypatch.setattr("graph.nodes.railpack_build_repair.get_railpack_version", lambda: "0.26.1")
    monkeypatch.setattr(
        "graph.nodes.railpack_build_repair.resolve_railpack_target",
        lambda repo_dir, _unit_root: type(
            "Target",
            (),
            {
                "railpack_dir": repo_dir,
                "config_dir": repo_dir,
                "build_cmd": None,
                "start_cmd": None,
            },
        )(),
    )
    monkeypatch.setattr(
        "graph.nodes.railpack_build_repair.target_to_meta",
        lambda _target: {"railpack_dir": str(tmp_path)},
    )
    monkeypatch.setattr(
        "graph.nodes.railpack_build_repair.run_railpack_prepare",
        lambda *_args, **_kwargs: (0, ""),
    )
    monkeypatch.setattr(
        "graph.nodes.railpack_build_repair.load_json_file",
        lambda _path: {"steps": []},
    )
    monkeypatch.setattr(
        "graph.nodes.railpack_build_repair.run_railpack_build",
        lambda *_args, **_kwargs: (124, "Railpack build timed out after 60s"),
    )

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
    assert "timeout cap" in out["build_verification"]["message"]
    assert out["build_verification"]["attempts"] == 1
    assert out["repair_history"] == []
    assert any(
        entry.get("node") == "railpack_build_repair" and entry.get("status") == "skipped"
        for entry in out["pipeline_trace"]
        if isinstance(entry, dict)
    )


def test_remote_builds_enabled_is_hardcoded():
    assert remote_builds.remote_builds_enabled() is True
    settings = remote_builds._settings()
    assert settings["region"] == "us-west-2"
    assert settings["project_name"] == "smartdeploy-builder"
    assert settings["source_bucket"] == "smart-deploy-codebuild-bucket"
    assert settings["wait_timeout_seconds"] == 60
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


def test_remote_build_manifest_uses_workspace_target_commands(tmp_path):
    repo_dir = str(tmp_path / "repo")
    defaults = {
        "web": {
            "unit_name": "web",
            "unit_root": "apps/web",
            "ecr_repository": "sd/my-repo/apps/web",
            "image_tag": "123456",
            "image_uri": "123456789012.dkr.ecr.us-east-1.amazonaws.com/sd/my-repo/apps/web:123456",
        }
    }

    manifest_unit = remote_builds._manifest_unit(
        repo_dir=repo_dir,
        unit={
            "name": "web",
            "root": "apps/web",
            "railpack_target": {
                "railpack_dir": repo_dir,
                "build_cmd": "pnpm --filter @hoplio/web run build",
                "start_cmd": "pnpm --filter @hoplio/web run start",
            },
        },
        meta=defaults["web"],
    )

    assert manifest_unit["railpack_dir"] == "."
    assert manifest_unit["unit_root"] == "apps/web"
    assert manifest_unit["build_cmd"] == "pnpm --filter @hoplio/web run build"
    assert manifest_unit["start_cmd"] == "pnpm --filter @hoplio/web run start"


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
    assert "RepositoryNotFoundException" in script
    assert "RepositoryAlreadyExistsException" in script
    assert "command_failure_reason" in script
    assert "ensure_repository(unit['repository_name'], region)" in script
    assert "build_target = unit.get('railpack_dir') or unit['unit_root']" in script
    assert "build_cmd.extend(['--build-cmd', unit['build_cmd']])" in script
    assert "build_cmd.extend(['--start-cmd', unit['start_cmd']])" in script


def test_remote_build_runner_tolerates_ecr_create_race(monkeypatch):
    namespace = {"__name__": "remote_build_runner_test"}
    exec(remote_builds._runner_script(), namespace)
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        if cmd[2] == "describe-repositories" and len(calls) == 1:
            return subprocess.CompletedProcess(
                cmd,
                254,
                stdout="",
                stderr="RepositoryNotFoundException: missing",
            )
        if cmd[2] == "create-repository":
            return subprocess.CompletedProcess(
                cmd,
                254,
                stdout="",
                stderr="RepositoryAlreadyExistsException: already exists",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(namespace["subprocess"], "run", fake_run)

    namespace["ensure_repository"]("sd/repo/root", "us-west-2")

    assert [call[2] for call in calls] == [
        "describe-repositories",
        "create-repository",
        "describe-repositories",
    ]


def test_remote_build_runner_does_not_create_after_ecr_describe_denied(monkeypatch):
    namespace = {"__name__": "remote_build_runner_test"}
    exec(remote_builds._runner_script(), namespace)
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd,
            254,
            stdout="",
            stderr="AccessDeniedException: not authorized",
        )

    monkeypatch.setattr(namespace["subprocess"], "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        namespace["ensure_repository"]("sd/repo/root", "us-west-2")

    assert [call[2] for call in calls] == ["describe-repositories"]
    assert "AccessDeniedException" in excinfo.value.stderr


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


def test_remote_build_log_excerpt_reads_top_level_cloudwatch_stream(monkeypatch):
    class FakeLogsClient:
        def __init__(self):
            self.calls = []

        def get_log_events(self, **kwargs):
            self.calls.append(kwargs)
            token = kwargs.get("nextToken")
            if token is None:
                return {
                    "events": [{"message": "first"}, {"message": "second"}],
                    "nextForwardToken": "page-2",
                }
            return {
                "events": [{"message": "third"}],
                "nextForwardToken": "page-2",
            }

    client = FakeLogsClient()
    monkeypatch.setattr(remote_builds, "_logs_client", lambda: client)
    monkeypatch.setattr(remote_builds, "_settings", lambda: {"max_log_lines": 2})

    excerpt = remote_builds._log_excerpt(
        {
            "logs": {
                "groupName": "/aws/codebuild/project",
                "streamName": "build-stream",
                "cloudWatchLogs": {"status": "ENABLED", "groupName": "/aws/codebuild/project"},
            }
        }
    )

    assert excerpt == "second\nthird"
    assert client.calls[0]["logGroupName"] == "/aws/codebuild/project"
    assert client.calls[0]["logStreamName"] == "build-stream"


def test_remote_build_log_excerpt_falls_back_to_phase_context(monkeypatch):
    monkeypatch.setattr(remote_builds, "_settings", lambda: {"max_log_lines": 5})

    excerpt = remote_builds._log_excerpt(
        {
            "phases": [
                {
                    "phaseType": "BUILD",
                    "phaseStatus": "FAILED",
                    "contexts": [
                        {
                            "statusCode": "COMMAND_EXECUTION_ERROR",
                            "message": "railpack build failed",
                        }
                    ],
                }
            ]
        }
    )

    assert excerpt == "[BUILD/FAILED] COMMAND_EXECUTION_ERROR: railpack build failed"


def test_remote_build_wait_marks_client_timeout(monkeypatch):
    monkeypatch.setattr(
        remote_builds,
        "_get_build",
        lambda _build_id: {"buildStatus": "IN_PROGRESS", "id": "project:build"},
    )
    stopped = {}
    monkeypatch.setattr(
        remote_builds,
        "_codebuild_client",
        lambda: type("Client", (), {"stop_build": lambda self, **kwargs: stopped.update(kwargs)})(),
    )

    build = remote_builds._wait_for_build("project:build", timeout_seconds=0, poll_interval_seconds=1)

    assert build["buildStatus"] == "TIMED_OUT"
    assert "Timed out waiting for CodeBuild build" in build["sdFailureReason"]
    assert stopped == {"id": "project:build"}


def test_remote_build_aggregate_status_treats_timeout_as_skip():
    assert remote_builds._aggregate_status(
        [{"status": "TIMED_OUT"}]
    ) == "skipped"
    assert remote_builds._aggregate_status(
        [{"status": "SUCCEEDED"}, {"status": "TIMED_OUT"}]
    ) == "partial"
