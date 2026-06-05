from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from graph.pipeline_trace import append_trace, trace_node
from tools.path_utils import join_repo_path, normalize_package_path, path_under_package


def _rel_to_package(file_path: str, package_path: str) -> Optional[str]:
    norm = normalize_package_path(file_path)
    package_norm = normalize_package_path(package_path)
    if package_norm == ".":
        return norm
    if not path_under_package(norm, package_norm):
        return None
    if norm == package_norm:
        return ""
    return norm[len(package_norm) + 1:]


def _detect_workspace_sub_packages(scan: Dict[str, Any], package_path: str) -> List[str]:
    """Detect workspace sub-package directories for pnpm/yarn/npm workspaces monorepos."""
    key_files = scan.get("key_files", {})
    if not isinstance(key_files, dict):
        return []

    package_norm = normalize_package_path(package_path)

    workspace_signals = {
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "lerna.json",
        "turbo.json",
        "nx.json",
        "yarn.lock",
    }
    has_workspace_file = False
    for file_path in key_files:
        rel = _rel_to_package(str(file_path), package_norm)
        if rel is None:
            continue
        depth = rel.count("/") if rel else 0
        basename = rel.split("/")[-1] if rel else rel
        if depth == 0 and basename in workspace_signals:
            has_workspace_file = True
            break

    if not has_workspace_file:
        root_pkg_key = "package.json" if package_norm == "." else f"{package_norm}/package.json"
        root_pkg_content = key_files.get(root_pkg_key, "")
        if '"workspaces"' not in root_pkg_content and '"packageManager"' not in root_pkg_content:
            return []

    sub_pkg_dirs: set[str] = set()
    for file_path in key_files:
        rel = _rel_to_package(str(file_path), package_norm)
        if rel is None:
            continue
        if rel.split("/")[-1] == "package.json" and "/" in rel:
            parent = "/".join(rel.split("/")[:-1])
            sub_pkg_dirs.add(parent)

    return sorted(sub_pkg_dirs)


def _has_existing_dockerfile(scan: Dict[str, Any], package_path: str) -> bool:
    key_files = scan.get("key_files", {})
    if not isinstance(key_files, dict):
        return False
    for path in key_files:
        rel = _rel_to_package(str(path), package_path)
        if rel is None:
            continue
        lower_name = rel.split("/")[-1].lower() if rel else str(path).split("/")[-1].lower()
        if lower_name == "dockerfile" or lower_name.startswith("dockerfile.") or lower_name.endswith(".dockerfile"):
            return True
    return False


def _scoped_key_files(scan: Dict[str, Any], package_path: str) -> Dict[str, str]:
    key_files = scan.get("key_files", {})
    if not isinstance(key_files, dict):
        return {}
    package_norm = normalize_package_path(package_path)
    scoped: Dict[str, str] = {}
    for path, content in key_files.items():
        rel = _rel_to_package(str(path), package_norm)
        if rel is not None:
            scoped[rel or "."] = content
    return scoped


def _read_package_json(scoped: Dict[str, str]) -> Dict[str, Any]:
    raw = scoped.get("package.json", "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _detect_framework(pkg: Dict[str, Any], scoped: Dict[str, str]) -> Optional[str]:
    deps: Dict[str, Any] = {}
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        section_data = pkg.get(section, {})
        if isinstance(section_data, dict):
            deps.update(section_data)

    dep_names = {k.lower() for k in deps}
    scripts = pkg.get("scripts", {}) if isinstance(pkg.get("scripts"), dict) else {}

    if "next" in dep_names or "next.config.js" in scoped or "next.config.mjs" in scoped or "next.config.ts" in scoped:
        return "next"
    if "vite" in dep_names or any(k.startswith("vite.config") for k in scoped):
        return "vite"
    if "astro" in dep_names or any(k.startswith("astro.config") for k in scoped):
        return "astro"
    if "react-scripts" in dep_names:
        return "cra"
    if "express" in dep_names:
        return "express"
    if "fastify" in dep_names:
        return "fastify"
    if "nuxt" in dep_names:
        return "nuxt"
    if "remix" in dep_names or "@remix-run/node" in dep_names:
        return "remix"
    if scripts.get("dev") and "vite" in str(scripts.get("dev", "")).lower():
        return "vite"
    return None


def _detect_provider(scoped: Dict[str, str], pkg: Dict[str, Any]) -> Tuple[str, Optional[str], int]:
    """Return (provider, framework, default_port)."""
    if "go.mod" in scoped:
        return "go", None, 8080
    if "pyproject.toml" in scoped or "requirements.txt" in scoped or "Pipfile" in scoped:
        return "python", None, 8000
    if "package.json" in scoped:
        framework = _detect_framework(pkg, scoped)
        if framework in {"vite", "astro", "cra"}:
            return "node", framework, 3000
        if framework == "next":
            return "node", framework, 3000
        return "node", framework, 3000
    if "index.html" in scoped:
        return "static", None, 80
    return "unknown", None, 8000


def _is_static_build(framework: Optional[str], pkg: Dict[str, Any]) -> bool:
    if framework in {"vite", "astro", "cra"}:
        return True
    scripts = pkg.get("scripts", {}) if isinstance(pkg.get("scripts"), dict) else {}
    build_script = str(scripts.get("build", "")).lower()
    return bool(build_script) and any(token in build_script for token in ("vite", "astro", "react-scripts", "next build"))


def _unit_name_from_root(root: str) -> str:
    norm = normalize_package_path(root)
    if norm == ".":
        return "app"
    return norm.split("/")[-1] or "app"


def _classify_single_unit(
    scan: Dict[str, Any],
    package_path: str,
    unit_root: str,
) -> Dict[str, Any]:
    scoped = _scoped_key_files(scan, unit_root)
    pkg = _read_package_json(scoped)
    provider, framework, port = _detect_provider(scoped, pkg)

    has_pkg = "package.json" in scoped
    has_html = "index.html" in scoped

    if _has_existing_dockerfile(scan, unit_root):
        unit_type = "existing_docker"
    elif not has_pkg and has_html:
        unit_type = "static"
    elif _is_static_build(framework, pkg):
        unit_type = "static_build"
    elif provider in {"node", "python", "go"}:
        unit_type = "server"
    elif has_pkg:
        unit_type = "server"
    else:
        unit_type = "server"

    return {
        "name": _unit_name_from_root(unit_root),
        "root": normalize_package_path(unit_root),
        "type": unit_type,
        "provider": provider,
        "framework": framework,
        "port": port,
        "artifacts": {
            "railpack_plan": None,
            "railpack_json": None,
        },
    }


def _classify_deploy_shape(units: List[Dict[str, Any]]) -> str:
    if len(units) > 1:
        return "multi"
    if not units:
        return "server"
    unit_type = units[0].get("type", "server")
    if unit_type == "existing_docker":
        return "existing_docker"
    if unit_type == "static":
        return "static"
    if unit_type == "static_build":
        return "static_build"
    return "server"


def classifier_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Classify deploy shape and deploy units from repo_scan + package_path."""
    with trace_node(state, "classifier"):
        if state.get("error"):
            append_trace(state, "classifier", "skipped", error=str(state.get("error")))
            return state

        scan = state.get("repo_scan", {})
        package_path = normalize_package_path(state.get("package_path", "."))

        if not scan or scan.get("error"):
            state["error"] = scan.get("error", "Missing repo scan data")
            return state

        if _has_existing_dockerfile(scan, package_path):
            unit = _classify_single_unit(scan, package_path, package_path)
            unit["type"] = "existing_docker"
            units = [unit]
            state["deploy_shape"] = "existing_docker"
            state["deploy_units"] = units
            return state

        sub_packages = _detect_workspace_sub_packages(scan, package_path)
        if sub_packages:
            units = []
            for rel in sub_packages:
                unit_root = package_path if package_path == "." else f"{package_path}/{rel}"
                units.append(_classify_single_unit(scan, package_path, unit_root))
            state["deploy_shape"] = "multi"
            state["deploy_units"] = units
            return state

        unit = _classify_single_unit(scan, package_path, package_path)
        state["deploy_shape"] = _classify_deploy_shape([unit])
        state["deploy_units"] = [unit]
        return state
