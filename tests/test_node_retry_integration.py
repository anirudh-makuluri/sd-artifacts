import json

from graph.nodes.planner import planner_node


class _Resp:
    def __init__(self, content: str):
        self.content = content


class _FakePlannerLLM:
    def __init__(self, invoke_fn):
        self._invoke_fn = invoke_fn

    def invoke(self, prompt: str):
        return self._invoke_fn(prompt)


def test_planner_retries_and_recovers(monkeypatch):
    calls = {"count": 0}

    def _invoke(_prompt: str):
        calls["count"] += 1
        if calls["count"] == 1:
            return _Resp("{not-json")
        payload = {
            "is_deployable": True,
            "error_reason": "",
            "detected_stack": "Python API",
            "services": [
                {
                    "name": "api",
                    "build_context": ".",
                    "port": 8000,
                    "dockerfile_path": "",
                }
            ],
            "has_existing_dockerfiles": False,
            "has_existing_compose": False,
        }
        return _Resp(json.dumps(payload))

    monkeypatch.setattr("graph.nodes.planner.llm_planner", _FakePlannerLLM(_invoke))

    state = {
        "repo_scan": {
            "key_files": {},
            "dirs": [],
        }
    }

    out = planner_node(state)

    assert "error" not in out
    assert out["detected_stack"] == "Python API"
    assert out["planner_retry_attempts"] == 2
    assert out["planner_fallback_used"] is False


def test_planner_returns_error_after_retry_exhaustion(monkeypatch):
    def _invoke(_prompt: str):
        return _Resp("{still-bad-json")

    monkeypatch.setattr("graph.nodes.planner.llm_planner", _FakePlannerLLM(_invoke))

    state = {
        "repo_scan": {
            "key_files": {},
            "dirs": [],
        }
    }

    out = planner_node(state)

    assert "error" in out
    assert "Failed to analyze repository" in out["error"]
