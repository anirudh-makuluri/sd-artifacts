from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from copy import deepcopy
from typing import Any, Dict, Optional, Tuple


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value)


def get_railpack_version() -> Optional[str]:
    """Return Railpack CLI version string, or None if not installed."""
    if shutil.which("railpack") is None:
        return None
    for args in (["--version"], ["version"], []):
        try:
            result = subprocess.run(
                ["railpack", *args],
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        if output:
            return output.splitlines()[0].strip()
    return None


def clone_repository(
    repo_url: str,
    github_token: Optional[str],
    commit_sha: str,
    dest_dir: str,
) -> Tuple[bool, str]:
    """Clone repo into dest_dir. Returns (success, error_message)."""
    os.makedirs(os.path.dirname(dest_dir) or ".", exist_ok=True)
    clone_url = repo_url
    if github_token and repo_url.startswith("https://github.com/"):
        clone_url = repo_url.replace("https://github.com/", f"https://{github_token}@github.com/")

    clone_cmd = ["git", "clone", "--depth", "1", clone_url, dest_dir]
    clone_result = subprocess.run(clone_cmd, text=True, capture_output=True, check=False)
    if clone_result.returncode != 0:
        logs = (clone_result.stdout or "") + "\n" + (clone_result.stderr or "")
        return False, logs.strip() or "git clone failed"

    if commit_sha and commit_sha != "unknown":
        checkout_result = subprocess.run(
            ["git", "-C", dest_dir, "checkout", commit_sha],
            text=True,
            capture_output=True,
            check=False,
        )
        if checkout_result.returncode != 0:
            logs = (checkout_result.stdout or "") + "\n" + (checkout_result.stderr or "")
            return False, logs.strip() or f"checkout {commit_sha} failed"

    return True, ""


def load_json_file(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_json_file(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def merge_railpack_json(unit_dir: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge patch into unit_dir/railpack.json and return merged dict."""
    railpack_path = os.path.join(unit_dir, "railpack.json")
    existing = load_json_file(railpack_path) or {}
    merged = _deep_merge(existing, patch or {})
    save_json_file(railpack_path, merged)
    return merged


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(base)
    for key, value in patch.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _build_env(env_overrides: Optional[Dict[str, str]]) -> Dict[str, str]:
    env = os.environ.copy()
    if env_overrides:
        env.update({k: str(v) for k, v in env_overrides.items()})
    return env


def _subprocess_output_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _combined_subprocess_output(stdout: Any, stderr: Any) -> str:
    return (_subprocess_output_text(stdout) + "\n" + _subprocess_output_text(stderr)).strip()


def run_railpack_prepare(
    unit_dir: str,
    plan_out_path: str,
    env_overrides: Optional[Dict[str, str]] = None,
    build_cmd: Optional[str] = None,
    start_cmd: Optional[str] = None,
) -> Tuple[int, str]:
    """Run railpack prepare. Returns (exit_code, combined_logs)."""
    if shutil.which("railpack") is None:
        return 127, "Railpack CLI is not installed on this host."

    os.makedirs(os.path.dirname(plan_out_path) or ".", exist_ok=True)
    cmd = ["railpack", "prepare", unit_dir, "--plan-out", plan_out_path]
    if build_cmd:
        cmd.extend(["--build-cmd", build_cmd])
    if start_cmd:
        cmd.extend(["--start-cmd", start_cmd])
    for key, value in (env_overrides or {}).items():
        cmd.extend(["--env", f"{key}={value}"])

    result = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        check=False,
        env=_build_env(env_overrides),
        cwd=unit_dir,
    )
    return result.returncode, _combined_subprocess_output(result.stdout, result.stderr)


def run_railpack_build(
    unit_dir: str,
    env_overrides: Optional[Dict[str, str]] = None,
    timeout_seconds: Optional[int] = None,
    build_cmd: Optional[str] = None,
    start_cmd: Optional[str] = None,
) -> Tuple[int, str]:
    """Run railpack build. Returns (exit_code, combined_logs)."""
    if shutil.which("railpack") is None:
        return 127, "Railpack CLI is not installed on this host."

    timeout = timeout_seconds or env_int("SD_RAILPACK_VERIFY_TIMEOUT_SECONDS", 300)
    cmd = ["railpack", "build", unit_dir]
    if build_cmd:
        cmd.extend(["--build-cmd", build_cmd])
    if start_cmd:
        cmd.extend(["--start-cmd", start_cmd])
    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
            env=_build_env(env_overrides),
            cwd=unit_dir,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        combined = _combined_subprocess_output(exc.stdout, exc.stderr)
        return 124, combined or f"Railpack build timed out after {timeout}s"

    return result.returncode, _combined_subprocess_output(result.stdout, result.stderr)
