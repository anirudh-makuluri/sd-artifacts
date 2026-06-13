from __future__ import annotations

import json
import os
import shutil
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover - optional unless remote builds are enabled
    boto3 = None

    class ClientError(Exception):
        pass

from tools.path_utils import normalize_package_path
from tools.railpack_tools import env_int


TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "FAULT", "STOPPED", "TIMED_OUT"}
ACTIVE_STATUSES = {"IN_PROGRESS", "QUEUED"}


def remote_builds_enabled() -> bool:
    return all(
        [
            os.getenv("SD_CODEBUILD_PROJECT_NAME"),
            os.getenv("SD_CODEBUILD_SOURCE_BUCKET"),
            os.getenv("SD_CODEBUILD_ECR_REPOSITORY_PREFIX"),
        ]
    )


def run_remote_railpack_builds(state: Dict[str, Any]) -> Dict[str, Any]:
    """Build deploy units remotely with CodeBuild and attach ECR metadata to state."""
    if not remote_builds_enabled():
        return state

    repo_dir = state.get("repo_dir")
    units = [unit for unit in state.get("deploy_units") or [] if isinstance(unit, dict)]
    if not repo_dir or not os.path.isdir(repo_dir) or not units:
        state["build_status"] = "error"
        state["build_verification"] = {
            "backend": "aws_codebuild_railpack",
            "status": "error",
            "message": "Remote build requested, but repo_dir or deploy_units were missing.",
            "attempts": 0,
            "duration_seconds": 0.0,
            "log_excerpt": "",
        }
        return state

    started = time.monotonic()
    defaults = _build_defaults(
        repo_url=str(state.get("repo_url") or ""),
        commit_sha=str(state.get("commit_sha") or "unknown"),
        units=units,
    )

    try:
        _ensure_boto3()
        settings = _settings()
        manifest = {"units": []}
        for unit in units:
            key = _unit_key(unit)
            meta = defaults.get(key)
            if not meta:
                continue
            if unit.get("type") == "existing_docker":
                meta["status"] = "SKIPPED"
                meta["failure_reason"] = "existing_docker deploy unit is not built by Railpack"
                unit["remote_build"] = meta
                continue
            manifest["units"].append(
                {
                    "unit_name": meta["unit_name"],
                    "unit_root": meta["unit_root"],
                    "repository_name": meta["ecr_repository"],
                    "image_tag": meta["image_tag"],
                    "image_uri": meta["image_uri"],
                }
            )

        if not manifest["units"]:
            state["remote_builds"] = defaults
            state["build_status"] = "skipped"
            state["build_verification"] = {
                "backend": "aws_codebuild_railpack",
                "status": "skipped",
                "message": "No Railpack deploy units required a remote build.",
                "attempts": 0,
                "duration_seconds": 0.0,
                "log_excerpt": "",
            }
            return state

        _ensure_ecr_repositories(manifest["units"])
        bundle_id = str(uuid.uuid4())
        source_key = _s3_key(settings["source_prefix"], state, f"{bundle_id}.zip")
        result_key = _s3_key(settings["result_prefix"], state, f"{bundle_id}.json")
        source_s3_uri = f"s3://{settings['source_bucket']}/{source_key}"
        result_s3_uri = f"s3://{settings['source_bucket']}/{result_key}"

        staging_dir = Path(repo_dir) / ".sd-remote-build"
        staging_dir.mkdir(parents=True, exist_ok=True)
        (staging_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (staging_dir / "run_build.py").write_text(_runner_script(), encoding="utf-8")
        (staging_dir / "buildspec.yml").write_text(_buildspec(), encoding="utf-8")

        zip_path = Path(repo_dir).parent / f"remote-build-{bundle_id}.zip"
        _zip_directory(Path(repo_dir), zip_path)
        _s3_client().upload_file(str(zip_path), settings["source_bucket"], source_key)
        zip_path.unlink(missing_ok=True)

        build = _start_build(source_bucket=settings["source_bucket"], source_key=source_key, result_s3_uri=result_s3_uri)
        build_id = str(build.get("id") or "")
        logs_url = _codebuild_logs_url(build)
        for meta in defaults.values():
            if meta.get("status") == "SKIPPED":
                continue
            meta["build_id"] = build_id
            meta["logs_url"] = logs_url
            meta["source_s3_uri"] = source_s3_uri
            meta["result_s3_uri"] = result_s3_uri
            meta["status"] = str(build.get("buildStatus") or "QUEUED")

        build = _wait_for_build(build_id, settings["wait_timeout_seconds"], settings["poll_interval_seconds"])
        remote_builds = _finalize(defaults=defaults, build=build, result_s3_uri=result_s3_uri)
        _attach_remote_builds(units, remote_builds)
        state["remote_builds"] = remote_builds
        state["build_status"] = _aggregate_status(remote_builds.values())
        state["build_verification"] = {
            "backend": "aws_codebuild_railpack",
            "status": state["build_status"],
            "message": "Railpack build completed in AWS CodeBuild.",
            "attempts": 1,
            "duration_seconds": round(time.monotonic() - started, 3),
            "log_excerpt": _first_log_excerpt(remote_builds.values()),
        }
        return state
    except Exception as exc:
        message = str(exc)
        for meta in defaults.values():
            if meta.get("status") != "SKIPPED":
                meta["status"] = "FAILED"
                meta["failure_reason"] = message
        _attach_remote_builds(units, defaults)
        state["remote_builds"] = defaults
        state["build_status"] = "failed"
        state["build_verification"] = {
            "backend": "aws_codebuild_railpack",
            "status": "failed",
            "message": message,
            "attempts": 1,
            "duration_seconds": round(time.monotonic() - started, 3),
            "log_excerpt": "",
        }
        return state


def _settings() -> Dict[str, Any]:
    return {
        "region": os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_REGION") or "us-east-1",
        "project_name": os.getenv("SD_CODEBUILD_PROJECT_NAME", "").strip(),
        "source_bucket": os.getenv("SD_CODEBUILD_SOURCE_BUCKET", "").strip(),
        "source_prefix": os.getenv("SD_CODEBUILD_SOURCE_PREFIX", "sd-remote-builds/source").strip().strip("/"),
        "result_prefix": os.getenv("SD_CODEBUILD_RESULT_PREFIX", "sd-remote-builds/results").strip().strip("/"),
        "ecr_repository_prefix": os.getenv("SD_CODEBUILD_ECR_REPOSITORY_PREFIX", "").strip().strip("/"),
        "wait_timeout_seconds": env_int("SD_CODEBUILD_WAIT_TIMEOUT_SECONDS", 900, minimum=30),
        "poll_interval_seconds": env_int("SD_CODEBUILD_POLL_INTERVAL_SECONDS", 10, minimum=1),
        "max_log_lines": env_int("SD_CODEBUILD_LOG_EXCERPT_LINES", 80, minimum=10),
    }


def _ensure_boto3() -> None:
    if boto3 is None:
        raise RuntimeError("boto3 is required when SD_CODEBUILD_* remote builds are enabled")


def _client(name: str):
    _ensure_boto3()
    return boto3.client(name, region_name=_settings()["region"])


def _s3_client():
    return _client("s3")


def _codebuild_client():
    return _client("codebuild")


def _ecr_client():
    return _client("ecr")


def _logs_client():
    return _client("logs")


def _registry_uri() -> str:
    identity = _client("sts").get_caller_identity()
    return f"{identity['Account']}.dkr.ecr.{_settings()['region']}.amazonaws.com"


def _build_defaults(*, repo_url: str, commit_sha: str, units: list[dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    repo_slug = _repo_name_slug(repo_url)
    registry = _registry_uri()
    defaults: Dict[str, Dict[str, Any]] = {}
    for unit in units:
        key = _unit_key(unit)
        unit_root = normalize_package_path(str(unit.get("root") or "."))
        repository_name = _repository_name(repo_slug, unit_root)
        image_tag = _image_tag(commit_sha)
        defaults[key] = {
            "provider": "aws_codebuild",
            "backend": "railpack",
            "status": "PENDING",
            "project_name": _settings()["project_name"],
            "build_id": None,
            "logs_url": None,
            "source_s3_uri": None,
            "result_s3_uri": None,
            "unit_name": key,
            "unit_root": unit_root,
            "ecr_repository": repository_name,
            "image_tag": image_tag,
            "image_uri": f"{registry}/{repository_name}:{image_tag}",
            "image_digest": None,
            "failure_reason": None,
            "log_excerpt": None,
        }
    return defaults


def _runner_script() -> str:
    return """from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], *, input_text: str | None = None, capture_output: bool = False, cwd: str | None = None) -> str:
    result = subprocess.run(cmd, check=True, text=True, input=input_text, capture_output=capture_output, cwd=cwd)
    return result.stdout.strip() if capture_output else ''


def main() -> int:
    manifest = json.loads(Path('.sd-remote-build/manifest.json').read_text(encoding='utf-8'))
    units = manifest.get('units', [])
    region = os.getenv('AWS_DEFAULT_REGION') or os.getenv('AWS_REGION') or 'us-east-1'
    registry = os.getenv('SD_REMOTE_BUILD_REGISTRY')
    result_path = Path(os.getenv('SD_REMOTE_BUILD_RESULT_PATH', '.sd-remote-build/remote-build-result.json'))
    if not registry:
        raise RuntimeError('SD_REMOTE_BUILD_REGISTRY is required')

    password = run(['aws', 'ecr', 'get-login-password', '--region', region], capture_output=True)
    run(['docker', 'login', '--username', 'AWS', '--password-stdin', registry], input_text=password)

    results = {}
    failed = False
    for unit in units:
        entry = dict(unit)
        entry['status'] = 'IN_PROGRESS'
        entry['image_digest'] = None
        entry['failure_reason'] = None
        try:
            run(['railpack', 'build', unit['unit_root'], '--name', unit['image_uri']])
            run(['docker', 'push', unit['image_uri']])
            digest = run(
                [
                    'aws', 'ecr', 'describe-images',
                    '--repository-name', unit['repository_name'],
                    '--image-ids', f\"imageTag={unit['image_tag']}\",
                    '--query', 'imageDetails[0].imageDigest',
                    '--output', 'text',
                    '--region', region,
                ],
                capture_output=True,
            )
            entry['status'] = 'SUCCEEDED'
            entry['image_digest'] = None if digest in {'', 'None', 'null'} else digest
        except subprocess.CalledProcessError as exc:
            failed = True
            entry['status'] = 'FAILED'
            entry['failure_reason'] = f\"Command failed ({exc.returncode}): {' '.join(map(str, exc.cmd))}\"
        results[unit['unit_name']] = entry

    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps({'units': results}, indent=2), encoding='utf-8')
    result_s3_uri = os.getenv('SD_REMOTE_BUILD_RESULT_S3_URI')
    if result_s3_uri:
        run(['aws', 's3', 'cp', str(result_path), result_s3_uri])
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
"""


def _buildspec() -> str:
    return """version: 0.2

phases:
  build:
    commands:
      - |
        if command -v python3 >/dev/null 2>&1; then
          PYTHON_BIN=python3
        else
          PYTHON_BIN=python
        fi
        "$PYTHON_BIN" .sd-remote-build/run_build.py
"""


def _start_build(*, source_bucket: str, source_key: str, result_s3_uri: str) -> Dict[str, Any]:
    build = _codebuild_client().start_build(
        projectName=_settings()["project_name"],
        sourceTypeOverride="S3",
        sourceLocationOverride=f"{source_bucket}/{source_key}",
        buildspecOverride=".sd-remote-build/buildspec.yml",
        privilegedModeOverride=True,
        environmentVariablesOverride=[
            {"name": "SD_REMOTE_BUILD_RESULT_S3_URI", "value": result_s3_uri, "type": "PLAINTEXT"},
            {"name": "SD_REMOTE_BUILD_RESULT_PATH", "value": ".sd-remote-build/remote-build-result.json", "type": "PLAINTEXT"},
            {"name": "SD_REMOTE_BUILD_REGISTRY", "value": _registry_uri(), "type": "PLAINTEXT"},
        ],
    )["build"]
    return build


def _wait_for_build(build_id: str, timeout_seconds: int, poll_interval_seconds: int) -> Dict[str, Any]:
    deadline = time.time() + timeout_seconds
    build = _get_build(build_id)
    while str(build.get("buildStatus") or "") in ACTIVE_STATUSES and time.time() < deadline:
        time.sleep(poll_interval_seconds)
        build = _get_build(build_id)
    return build


def _get_build(build_id: str) -> Dict[str, Any]:
    builds = _codebuild_client().batch_get_builds(ids=[build_id]).get("builds", [])
    if not builds:
        raise RuntimeError(f"CodeBuild build not found: {build_id}")
    return builds[0]


def _finalize(
    *,
    defaults: Dict[str, Dict[str, Any]],
    build: Dict[str, Any],
    result_s3_uri: str,
) -> Dict[str, Dict[str, Any]]:
    status = str(build.get("buildStatus") or "UNKNOWN")
    build_id = str(build.get("id") or "")
    logs_url = _codebuild_logs_url(build)
    result_map = _read_result_map(result_s3_uri) if status == "SUCCEEDED" else {}
    log_excerpt = _log_excerpt(build) if status in TERMINAL_STATUSES and status != "SUCCEEDED" else None

    finalized: Dict[str, Dict[str, Any]] = {}
    for key, meta in defaults.items():
        entry = dict(meta)
        if entry.get("status") == "SKIPPED":
            finalized[key] = entry
            continue
        entry["status"] = status
        entry["build_id"] = build_id or entry.get("build_id")
        entry["logs_url"] = logs_url or entry.get("logs_url")
        result_entry = result_map.get(key)
        if isinstance(result_entry, dict):
            entry.update(
                {
                    "status": result_entry.get("status", entry["status"]),
                    "image_uri": result_entry.get("image_uri", entry.get("image_uri")),
                    "image_digest": result_entry.get("image_digest", entry.get("image_digest")),
                    "failure_reason": result_entry.get("failure_reason", entry.get("failure_reason")),
                }
            )
        if entry.get("status") != "SUCCEEDED" and entry.get("status") not in ACTIVE_STATUSES:
            entry["failure_reason"] = entry.get("failure_reason") or status
            entry["log_excerpt"] = log_excerpt
        finalized[key] = entry
    return finalized


def _read_result_map(result_s3_uri: str) -> Dict[str, Dict[str, Any]]:
    bucket, key = _parse_s3_uri(result_s3_uri)
    if not bucket or not key:
        return {}
    try:
        response = _s3_client().get_object(Bucket=bucket, Key=key)
        payload = json.loads(response["Body"].read().decode("utf-8"))
        units = payload.get("units", {})
        return units if isinstance(units, dict) else {}
    except ClientError:
        return {}


def _ensure_ecr_repositories(units: list[dict[str, Any]]) -> None:
    client = _ecr_client()
    for unit in units:
        repository_name = str(unit.get("repository_name") or "")
        if not repository_name:
            continue
        try:
            client.describe_repositories(repositoryNames=[repository_name])
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "RepositoryNotFoundException":
                raise
            client.create_repository(repositoryName=repository_name)


def _log_excerpt(build: Dict[str, Any]) -> str | None:
    logs = build.get("logs", {}) if isinstance(build.get("logs"), dict) else {}
    cloudwatch = logs.get("cloudWatchLogs", {}) if isinstance(logs.get("cloudWatchLogs"), dict) else {}
    group_name = cloudwatch.get("groupName")
    stream_name = cloudwatch.get("streamName")
    if not group_name or not stream_name:
        return None
    try:
        response = _logs_client().get_log_events(logGroupName=group_name, logStreamName=stream_name, startFromHead=True)
        lines = [str(event.get("message") or "").rstrip() for event in response.get("events", []) if event.get("message")]
        return "\n".join(lines[-_settings()["max_log_lines"] :]) if lines else None
    except ClientError:
        return None


def _codebuild_logs_url(build: Dict[str, Any]) -> str | None:
    build_id = str(build.get("id") or "")
    project_name = str(build.get("projectName") or _settings()["project_name"])
    if not build_id or not project_name:
        return None
    region = _settings()["region"]
    encoded_id = urllib.parse.quote(build_id, safe="")
    return f"https://{region}.console.aws.amazon.com/codesuite/codebuild/{project_name}/build/{encoded_id}/?region={region}"


def _zip_directory(source_dir: Path, zip_path: Path) -> None:
    archive_base = str(zip_path.with_suffix(""))
    shutil.make_archive(archive_base, "zip", root_dir=source_dir)


def _attach_remote_builds(units: list[dict[str, Any]], remote_builds: Dict[str, Dict[str, Any]]) -> None:
    for unit in units:
        key = _unit_key(unit)
        if key in remote_builds:
            unit["remote_build"] = remote_builds[key]


def _aggregate_status(values: Iterable[Dict[str, Any]]) -> str:
    statuses = [str(value.get("status") or "") for value in values if isinstance(value, dict)]
    buildable = [status for status in statuses if status != "SKIPPED"]
    if not buildable:
        return "skipped"
    if all(status == "SUCCEEDED" for status in buildable):
        return "passed"
    if any(status == "SUCCEEDED" for status in buildable):
        return "partial"
    return "failed"


def _first_log_excerpt(values: Iterable[Dict[str, Any]]) -> str:
    for value in values:
        excerpt = value.get("log_excerpt") if isinstance(value, dict) else None
        if excerpt:
            return str(excerpt)
    return ""


def _unit_key(unit: Dict[str, Any]) -> str:
    return str(unit.get("name") or normalize_package_path(str(unit.get("root") or "."))).strip() or "root"


def _repo_slug(repo_url: str) -> str:
    tail = repo_url.rstrip("/").replace(".git", "")
    if "github.com/" in tail:
        tail = tail.split("github.com/", 1)[1]
    return _sanitize_segment(tail.replace("/", "-"), fallback="repo")


def _repo_name_slug(repo_url: str) -> str:
    value = str(repo_url or "").strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    parsed = urllib.parse.urlparse(value)
    path = parsed.path or value
    if ":" in path and not path.startswith("/"):
        path = path.split(":", 1)[1]
    parts = [part for part in path.strip("/").split("/") if part]
    repo_name = parts[-1] if parts else value
    return _sanitize_segment(repo_name, fallback="repo")


def _sanitize_segment(value: str, fallback: str) -> str:
    lowered = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    collapsed = "-".join(part for part in lowered.split("-") if part)
    if not collapsed:
        collapsed = fallback
    if not collapsed[0].isalpha():
        collapsed = f"a-{collapsed}"
    return collapsed[:80].rstrip("-")


def _repository_name(repo_slug: str, package_path: str) -> str:
    prefix = _settings()["ecr_repository_prefix"]
    package_norm = normalize_package_path(package_path)
    package_segments = [] if package_norm == "." else package_norm.split("/")
    pieces = [
        _sanitize_segment(piece, fallback="path")
        for piece in [prefix, repo_slug, *package_segments]
        if piece and piece.strip("/")
    ]
    return "/".join(pieces)[:256]


def _image_tag(commit_sha: str) -> str:
    sha = "".join(
        ch.lower()
        for ch in str(commit_sha or "unknown")[:6]
        if ch.isalnum() or ch in "._-"
    )
    return sha or "unknown"


def _s3_key(prefix: str, state: Dict[str, Any], suffix: str) -> str:
    repo = _repo_slug(str(state.get("repo_url") or "repo"))
    sha = str(state.get("commit_sha") or "unknown")[:12] or "unknown"
    package = _sanitize_segment(normalize_package_path(str(state.get("package_path") or ".")).replace("/", "-"), fallback="root")
    return "/".join(part for part in [prefix, repo, sha, package, suffix] if part)


def _parse_s3_uri(uri: str) -> tuple[str | None, str | None]:
    if not uri.startswith("s3://"):
        return None, None
    bucket, _, key = uri[5:].partition("/")
    return bucket or None, key or None
