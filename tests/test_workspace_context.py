import json
import os
import tempfile

from tools.workspace_context import resolve_railpack_target


def test_resolve_railpack_target_uses_repo_root_for_pnpm_workspace():
    with tempfile.TemporaryDirectory() as repo_dir:
        os.makedirs(os.path.join(repo_dir, "apps", "web"))
        with open(os.path.join(repo_dir, "pnpm-workspace.yaml"), "w", encoding="utf-8") as fh:
            fh.write("packages:\n  - apps/*\n  - packages/*\n")
        with open(os.path.join(repo_dir, "pnpm-lock.yaml"), "w", encoding="utf-8") as fh:
            fh.write("")
        with open(os.path.join(repo_dir, "package.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "root", "private": True, "packageManager": "pnpm@10.0.0"}, fh)
        with open(os.path.join(repo_dir, "apps", "web", "package.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "name": "@hoplio/web",
                    "scripts": {"build": "next build", "start": "next start"},
                    "dependencies": {"@hoplio/tsconfig": "workspace:*"},
                },
                fh,
            )

        target = resolve_railpack_target(repo_dir, "apps/web")

        assert target.is_workspace is True
        assert target.railpack_dir == repo_dir
        assert target.package_manager == "pnpm"
        assert target.package_name == "@hoplio/web"
        assert target.build_cmd == "pnpm --filter @hoplio/web run build"
        assert target.start_cmd == "pnpm --filter @hoplio/web run start"


def test_resolve_railpack_target_keeps_unit_dir_for_simple_app():
    with tempfile.TemporaryDirectory() as repo_dir:
        with open(os.path.join(repo_dir, "package.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "name": "demo",
                    "scripts": {"build": "vite build", "start": "vite preview"},
                },
                fh,
            )

        target = resolve_railpack_target(repo_dir, ".")

        assert target.is_workspace is False
        assert target.railpack_dir == repo_dir
        assert target.build_cmd == "npm run build"
        assert target.start_cmd == "npm run start"


def test_resolve_railpack_target_uses_unit_dir_for_go_in_pnpm_monorepo():
    with tempfile.TemporaryDirectory() as repo_dir:
        os.makedirs(os.path.join(repo_dir, "apps", "backend", "cmd"))
        with open(os.path.join(repo_dir, "pnpm-workspace.yaml"), "w", encoding="utf-8") as fh:
            fh.write("packages:\n  - apps/*\n")
        with open(os.path.join(repo_dir, "pnpm-lock.yaml"), "w", encoding="utf-8") as fh:
            fh.write("")
        with open(os.path.join(repo_dir, "package.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "limity", "private": True, "packageManager": "pnpm@10.0.0"}, fh)
        with open(os.path.join(repo_dir, "apps", "backend", "go.mod"), "w", encoding="utf-8") as fh:
            fh.write("module example.com/backend\n\ngo 1.23\n")

        target = resolve_railpack_target(repo_dir, "apps/backend")

        assert target.is_workspace is False
        assert target.railpack_dir == os.path.join(repo_dir, "apps", "backend")
        assert target.config_dir == target.railpack_dir
        assert target.build_cmd is None
        assert target.start_cmd is None
        assert target.package_name is None
