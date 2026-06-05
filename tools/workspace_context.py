from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from tools.path_utils import join_repo_path, normalize_package_path
from tools.railpack_tools import load_json_file


@dataclass(frozen=True)
class RailpackTarget:
    """Resolved Railpack CLI directory and commands for a deploy unit."""

    railpack_dir: str
    config_dir: str
    unit_root: str
    is_workspace: bool
    package_manager: Optional[str]
    package_name: Optional[str]
    build_cmd: Optional[str]
    start_cmd: Optional[str]


def _read_package_json(path: str) -> Dict[str, Any]:
    data = load_json_file(path)
    return data if isinstance(data, dict) else {}


def _detect_package_manager(workspace_root: str) -> Optional[str]:
    if os.path.isfile(os.path.join(workspace_root, "pnpm-lock.yaml")):
        return "pnpm"
    if os.path.isfile(os.path.join(workspace_root, "bun.lock")) or os.path.isfile(
        os.path.join(workspace_root, "bun.lockb")
    ):
        return "bun"
    if os.path.isfile(os.path.join(workspace_root, "yarn.lock")):
        return "yarn"
    if os.path.isfile(os.path.join(workspace_root, "package-lock.json")):
        return "npm"

    root_pkg = _read_package_json(os.path.join(workspace_root, "package.json"))
    manager = str(root_pkg.get("packageManager") or "")
    if manager.startswith("pnpm"):
        return "pnpm"
    if manager.startswith("yarn"):
        return "yarn"
    if manager.startswith("bun"):
        return "bun"
    if manager.startswith("npm"):
        return "npm"
    if root_pkg.get("workspaces"):
        return "npm"
    return None


def _has_workspace_at_root(repo_dir: str) -> bool:
    if os.path.isfile(os.path.join(repo_dir, "pnpm-workspace.yaml")):
        return True
    root_pkg = _read_package_json(os.path.join(repo_dir, "package.json"))
    workspaces = root_pkg.get("workspaces")
    if isinstance(workspaces, list) and workspaces:
        return True
    if isinstance(workspaces, dict) and workspaces.get("packages"):
        return True
    return False


def _package_json_has_workspace_deps(unit_dir: str) -> bool:
    pkg = _read_package_json(os.path.join(unit_dir, "package.json"))
    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        deps = pkg.get(section, {})
        if not isinstance(deps, dict):
            continue
        for version in deps.values():
            if isinstance(version, str) and version.startswith("workspace:"):
                return True
    return False


def _filter_build_cmd(package_manager: str, package_name: str, script: str) -> str:
    if package_manager == "pnpm":
        return f"pnpm --filter {package_name} run {script}"
    if package_manager == "yarn":
        return f"yarn workspace {package_name} run {script}"
    if package_manager == "npm":
        return f"npm run {script} -w {package_name}"
    if package_manager == "bun":
        return f"bun run --filter {package_name} {script}"
    return f"npm run {script} -w {package_name}"


def _script_from_pkg(unit_dir: str, script_name: str) -> Optional[str]:
    pkg = _read_package_json(os.path.join(unit_dir, "package.json"))
    scripts = pkg.get("scripts", {})
    if isinstance(scripts, dict) and scripts.get(script_name):
        return script_name
    return None


def _unit_has_package_json(unit_dir: str) -> bool:
    return os.path.isfile(os.path.join(unit_dir, "package.json"))


def _should_use_workspace(repo_dir: str, unit_norm: str, unit_dir: str, unit_pkg: Dict[str, Any]) -> bool:
    """Use monorepo root + filter commands only for named Node workspace packages."""
    if unit_norm == ".":
        return False
    if not _unit_has_package_json(unit_dir):
        return False
    if not _has_workspace_at_root(repo_dir):
        return False
    package_name = str(unit_pkg.get("name") or "").strip()
    return bool(package_name)


def resolve_railpack_target(repo_dir: str, unit_root: str) -> RailpackTarget:
    """Choose Railpack directory and workspace filter commands for a deploy unit."""
    unit_norm = normalize_package_path(unit_root)
    unit_dir = join_repo_path(repo_dir, unit_norm)
    unit_pkg = _read_package_json(os.path.join(unit_dir, "package.json"))
    package_name = str(unit_pkg.get("name") or "").strip() or None

    use_workspace = _should_use_workspace(repo_dir, unit_norm, unit_dir, unit_pkg)
    if not use_workspace:
        build_script = _script_from_pkg(unit_dir, "build")
        start_script = _script_from_pkg(unit_dir, "start")
        return RailpackTarget(
            railpack_dir=unit_dir,
            config_dir=unit_dir,
            unit_root=unit_norm,
            is_workspace=False,
            package_manager=None,
            package_name=package_name,
            build_cmd=f"npm run {build_script}" if build_script else None,
            start_cmd=f"npm run {start_script}" if start_script else None,
        )

    package_manager = _detect_package_manager(repo_dir) or "pnpm"
    build_script = _script_from_pkg(unit_dir, "build")
    start_script = _script_from_pkg(unit_dir, "start")
    build_cmd = (
        _filter_build_cmd(package_manager, package_name, build_script)
        if package_name and build_script
        else None
    )
    start_cmd = (
        _filter_build_cmd(package_manager, package_name, start_script)
        if package_name and start_script
        else None
    )

    return RailpackTarget(
        railpack_dir=repo_dir,
        config_dir=repo_dir,
        unit_root=unit_norm,
        is_workspace=True,
        package_manager=package_manager,
        package_name=package_name,
        build_cmd=build_cmd,
        start_cmd=start_cmd,
    )


def target_to_meta(target: RailpackTarget) -> Dict[str, Any]:
    return {
        "is_workspace": target.is_workspace,
        "railpack_dir": target.railpack_dir,
        "unit_root": target.unit_root,
        "package_manager": target.package_manager,
        "package_name": target.package_name,
        "build_cmd": target.build_cmd,
        "start_cmd": target.start_cmd,
    }
