import json
import os
import subprocess
import sys
from copy import deepcopy

import pytest
from botocore.exceptions import (
    ClientError,
    NoCredentialsError,
    NoRegionError,
    PartialCredentialsError,
)
from fastapi.testclient import TestClient
from langgraph.graph import END, StateGraph

import app as app_module
import db as db_module
from graph.bedrock_errors import BedrockUnavailableError, is_bedrock_unavailable
from graph.llm_retry import RetryConfig, RetryExhaustedError, invoke_with_retry
from graph.nodes import llm_config
from graph.nodes.deploy_briefing import deploy_briefing_node
from graph.nodes.finalize import finalize_node
from graph.nodes.railpack_build_repair import (
    _invoke_repair_llm,
    railpack_build_repair_node,
)


def _denied(code="AccessDeniedException"):
    return ClientError(
        {"Error": {"Code": code, "Message": "private provider details"}}, "InvokeModel"
    )


def _state():
    return {
        "commit_sha": "known-sha",
        "package_path": ".",
        "deploy_shape": "server",
        "build_status": "passed",
        "deploy_units": [
            {
                "name": "api",
                "root": ".",
                "type": "server",
                "port": 8000,
                "artifacts": {
                    "railpack_plan": {
                        "steps": [
                            {"name": "build", "commands": [{"cmd": "npm run build"}]}
                        ],
                        "deploy": {"startCommand": "npm start"},
                    }
                },
            }
        ],
    }


@pytest.fixture
def reset_clients(monkeypatch):
    # Modify the dict directly so monkeypatch does not trigger module __getattr__.
    for name in ("llm_briefing", "llm_repair"):
        monkeypatch.setitem(llm_config.__dict__, name, None)
        monkeypatch.delitem(llm_config.__dict__, name)


@pytest.mark.parametrize(
    "failure",
    [
        _denied(),
        _denied("ValidationException"),
        _denied("ResourceNotFoundException"),
        _denied("ExpiredTokenException"),
        _denied("UnrecognizedClientException"),
        NoCredentialsError(),
        NoRegionError(),
        PartialCredentialsError(provider="env", cred_var="AWS_SECRET_ACCESS_KEY"),
        ValueError(
            "Error raised by bedrock service: AccessDeniedException: private provider details"
        ),
    ],
)
def test_briefing_configuration_failures_are_quiet_and_not_retried(
    monkeypatch, failure
):
    calls = []

    class UnavailableClient:
        def invoke(self, prompt):
            calls.append(prompt)
            raise failure

    monkeypatch.setitem(llm_config.__dict__, "llm_briefing", UnavailableClient())
    out = deploy_briefing_node(_state())
    assert len(calls) == 1
    assert not out.get("error") and not out.get("errors")
    assert out["build_status"] == "passed"
    assert "npm run build" in out["deploy_briefing"]
    assert "npm start" in out["deploy_briefing"]
    assert out["llm_outputs"]["briefing"] == {"source": "deterministic"}
    assert all(entry.get("error") is None for entry in out["pipeline_trace"])
    assert "private provider details" not in json.dumps(out)
    # A later optional repair in the same request should not call Bedrock again.
    with pytest.raises(BedrockUnavailableError) as caught:
        _invoke_repair_llm(out, "repair")
    assert is_bedrock_unavailable(caught.value)


@pytest.mark.parametrize(
    "failure", [NoRegionError(), ValueError("invalid client configuration")]
)
def test_client_initialization_failure_uses_quiet_fallback(
    monkeypatch, reset_clients, failure
):
    calls = []

    def unavailable_constructor(**kwargs):
        calls.append(kwargs)
        raise failure

    monkeypatch.setattr(llm_config, "ChatBedrock", unavailable_constructor)
    out = deploy_briefing_node(_state())
    assert len(calls) == 1
    assert not out.get("errors")
    assert out["llm_outputs"]["briefing"] == {"source": "deterministic"}


def test_missing_optional_sdk_uses_quiet_fallback(monkeypatch, reset_clients):
    monkeypatch.setattr(llm_config, "ChatBedrock", None)
    out = deploy_briefing_node(_state())
    assert not out.get("errors")
    assert out["llm_outputs"]["briefing"] == {"source": "deterministic"}


def test_importing_app_does_not_initialize_bedrock():
    code = """
import langchain_aws
def unavailable(**kwargs):
    raise AssertionError('Bedrock must not initialize at import time')
langchain_aws.ChatBedrock = unavailable
import app
"""
    env = dict(os.environ, PYTHON_DOTENV_DISABLED="1", AWS_EC2_METADATA_DISABLED="true")
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_wrapped_permission_failure_is_recognized_without_retry():
    calls = []

    def invoke(_prompt):
        calls.append(True)
        try:
            raise _denied()
        except ClientError as exc:
            raise RuntimeError("wrapped") from exc

    with pytest.raises(RuntimeError, match="wrapped"):
        invoke_with_retry(invoke_fn=invoke, prompt="test")
    assert len(calls) == 1


def test_transient_bedrock_errors_keep_retry_behavior():
    calls = []

    def invoke(_prompt):
        calls.append(True)
        raise _denied("ThrottlingException")

    with pytest.raises(RetryExhaustedError):
        invoke_with_retry(
            invoke_fn=invoke,
            prompt="test",
            config=RetryConfig(max_attempts=2, backoff_base_seconds=0, jitter_ratio=0),
        )
    assert len(calls) == 2


def test_unrelated_llm_failures_still_have_diagnostics(monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("unexpected model response")

    monkeypatch.setattr("graph.nodes.deploy_briefing.invoke_with_retry", fail)
    out = deploy_briefing_node(_state())
    assert "unexpected model response" in out["errors"][0]
    assert not out.get("_bedrock_unavailable")


@pytest.mark.parametrize("endpoint", ["/analyze", "/analyze/stream"])
def test_api_completes_without_bedrock_errors(monkeypatch, endpoint):
    class UnavailableClient:
        def invoke(self, prompt):
            raise _denied()

    monkeypatch.setenv("SD_API_BEARER_TOKEN", "local-test")
    monkeypatch.setattr(db_module, "supabase", None)
    monkeypatch.setitem(llm_config.__dict__, "llm_briefing", UnavailableClient())
    workflow = StateGraph(dict)
    workflow.add_node("prepare", lambda state: {**state, **_state()})
    workflow.add_node("deploy_briefing", deploy_briefing_node)
    workflow.add_node("finalize", finalize_node)
    workflow.set_entry_point("prepare")
    workflow.add_edge("prepare", "deploy_briefing")
    workflow.add_edge("deploy_briefing", "finalize")
    workflow.add_edge("finalize", END)
    monkeypatch.setattr(app_module, "graph", workflow.compile())
    response = TestClient(app_module.app).post(
        endpoint,
        json={"repo_url": "https://github.com/acme/repo"},
        headers={"Authorization": "Bearer local-test"},
    )
    assert response.status_code == 200
    if endpoint.endswith("/stream"):
        assert "event: error" not in response.text
        final_event = response.text.strip().split("\n\n")[-1]
        assert final_event.startswith("event: complete")
        payload = json.loads(final_event.split("data: ", 1)[1])
    else:
        payload = response.json()
    assert payload["build_status"] == "passed"
    assert payload["errors"] == []
    assert "npm start" in payload["deploy_briefing"]
    assert "private provider details" not in response.text
    assert "_bedrock_unavailable" not in response.text


def test_repair_denied_preserves_failed_build_without_provider_details(
    monkeypatch, tmp_path
):
    calls = []

    class UnavailableClient:
        def invoke(self, prompt):
            calls.append(prompt)
            raise _denied()

    monkeypatch.setitem(llm_config.__dict__, "llm_repair", UnavailableClient())
    monkeypatch.setenv("SD_SKIP_RAILPACK_BUILD", "false")
    monkeypatch.setattr(
        "graph.nodes.railpack_build_repair.remote_builds_enabled", lambda: False
    )
    monkeypatch.setattr(
        "graph.nodes.railpack_build_repair.get_railpack_version", lambda: "test-version"
    )
    monkeypatch.setattr(
        "graph.nodes.railpack_build_repair.run_railpack_prepare",
        lambda *args, **kwargs: (0, ""),
    )
    monkeypatch.setattr(
        "graph.nodes.railpack_build_repair.load_json_file", lambda *args: {"steps": []}
    )
    monkeypatch.setattr(
        "graph.nodes.railpack_build_repair.run_railpack_build",
        lambda *args, **kwargs: (1, "actual build failure"),
    )
    state = _state()
    state["repo_dir"] = str(tmp_path)
    state["deploy_units"].append(deepcopy(state["deploy_units"][0]))
    state["deploy_units"][1]["name"] = "second"
    out = railpack_build_repair_node(state)
    assert len(calls) == 1
    assert out["build_status"] == "failed"
    assert out["build_verification"]["log_excerpt"] == "actual build failure"
    assert not out.get("error") and not out.get("errors")
    assert out["llm_outputs"]["repair"] == {"source": "skipped"}
    assert "private provider details" not in json.dumps(out)
    assert all(
        item["diagnosis"] == "Automatic repair skipped."
        for item in out["repair_history"]
    )
