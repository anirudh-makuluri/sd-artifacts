from graph.nodes.railpack_build_repair import (
    _build_verification_skip_reason,
    railpack_build_repair_node,
)


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
