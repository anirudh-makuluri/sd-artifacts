import subprocess

from tools.railpack_tools import run_railpack_build


def test_run_railpack_build_decodes_timeout_output(monkeypatch, tmp_path):
    monkeypatch.setattr("tools.railpack_tools.shutil.which", lambda _name: "/usr/bin/railpack")

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(
            cmd=["railpack", "build"],
            timeout=3,
            output=b"stdout bytes",
            stderr=b"stderr bytes",
        )

    monkeypatch.setattr("tools.railpack_tools.subprocess.run", fake_run)

    exit_code, logs = run_railpack_build(str(tmp_path), timeout_seconds=3)

    assert exit_code == 124
    assert logs == "stdout bytes\nstderr bytes"


def test_run_railpack_build_returns_timeout_message_without_output(monkeypatch, tmp_path):
    monkeypatch.setattr("tools.railpack_tools.shutil.which", lambda _name: "/usr/bin/railpack")

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(
            cmd=["railpack", "build"],
            timeout=7,
        )

    monkeypatch.setattr("tools.railpack_tools.subprocess.run", fake_run)

    exit_code, logs = run_railpack_build(str(tmp_path), timeout_seconds=7)

    assert exit_code == 124
    assert logs == "Railpack build timed out after 7s"


def test_run_railpack_build_uses_60_second_default_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr("tools.railpack_tools.shutil.which", lambda _name: "/usr/bin/railpack")
    captured = {}

    def fake_run(*_args, **kwargs):
        captured["timeout"] = kwargs["timeout"]
        return subprocess.CompletedProcess(kwargs["args"] if "args" in kwargs else _args[0], 0, "", "")

    monkeypatch.setattr("tools.railpack_tools.subprocess.run", fake_run)

    exit_code, logs = run_railpack_build(str(tmp_path))

    assert exit_code == 0
    assert logs == ""
    assert captured["timeout"] == 60
