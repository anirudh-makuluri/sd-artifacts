from __future__ import annotations


def normalize_package_path(path: str | None) -> str:
    """Normalize package paths to a stable representation."""
    normalized = (path or ".").replace("\\", "/").strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized or "."


def join_repo_path(repo_dir: str, package_path: str) -> str:
    """Join repo root with a normalized package path."""
    import os

    package_norm = normalize_package_path(package_path)
    if package_norm == ".":
        return repo_dir
    return os.path.join(repo_dir, *package_norm.split("/"))


def path_under_package(candidate_path: str, package_path: str) -> bool:
    """Return True when candidate_path is package_path or a descendant of it."""
    candidate_norm = normalize_package_path(candidate_path)
    package_norm = normalize_package_path(package_path)

    if package_norm == ".":
        return True
    if candidate_norm == package_norm:
        return True
    return candidate_norm.startswith(package_norm + "/")
