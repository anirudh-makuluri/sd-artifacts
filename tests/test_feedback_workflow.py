"""Smoke tests for v2 feedback graph (clone_repo -> railpack_build_repair -> finalize)."""

from graph.feedback import (
    build_feedback_initial_state,
    feedback_graph,
    run_feedback_improvement,
)


def _v2_cached_result(**overrides):
    base = {
        "schema_version": 2,
        "commit_sha": "abc123",
        "package_path": ".",
        "deploy_shape": "server",
        "build_status": "passed",
        "deploy_units": [
            {
                "name": "api",
                "root": ".",
                "type": "server",
                "framework": "fastapi",
                "port": 8000,
            }
        ],
        "deploy_briefing": "FastAPI service",
        "inputs_snapshot": {"repo_scan": {"language": "Python"}},
    }
    base.update(overrides)
    return base


def test_build_feedback_initial_state_carries_v2_fields():
    cached = _v2_cached_result()
    state = build_feedback_initial_state(
        cached,
        "use node 22",
        repo_url="https://github.com/acme/repo",
        package_path="services/api",
    )

    assert state["feedback"] == "use node 22"
    assert state["repo_url"] == "https://github.com/acme/repo"
    assert state["commit_sha"] == "abc123"
    assert state["package_path"] == "services/api"
    assert state["deploy_shape"] == "server"
    assert state["deploy_units"] == cached["deploy_units"]
    assert state["deploy_briefing"] == "FastAPI service"
    assert state["_skip_cache"] is True


def test_feedback_graph_has_v2_nodes():
    node_names = set(feedback_graph.get_graph().nodes.keys())
    assert {"clone_repo", "railpack_build_repair", "finalize"}.issubset(node_names)


def test_run_feedback_improvement_invokes_compiled_graph(monkeypatch):
    cached = _v2_cached_result()
    expected = _v2_cached_result(
        build_status="passed",
        deploy_briefing="Updated briefing",
        workflow_version="sd-artifacts@test",
    )

    monkeypatch.setattr(
        "graph.feedback.feedback_graph.invoke",
        lambda initial_state, config=None: {**initial_state, **expected},
    )

    out = run_feedback_improvement(
        cached,
        "refresh build",
        repo_url="https://github.com/acme/repo",
        package_path=".",
    )

    assert out["schema_version"] == 2
    assert out["deploy_briefing"] == "Updated briefing"
    assert out["build_status"] == "passed"
    assert out["feedback"] == "refresh build"
