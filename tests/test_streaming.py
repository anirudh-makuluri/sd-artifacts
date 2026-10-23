import asyncio
import json
import logging

import pytest

from services.streaming import bounded_analysis_events, stream_timeout_seconds


@pytest.mark.parametrize("raw", ["", "garbage", "0", "-1", "nan", "inf", "-inf"])
def test_invalid_timeout_uses_default(monkeypatch, raw):
    monkeypatch.setenv("SD_ANALYZE_STREAM_TIMEOUT_SECONDS", raw)
    assert stream_timeout_seconds() == 110


def test_timeout_is_configurable(monkeypatch):
    monkeypatch.setenv("SD_ANALYZE_STREAM_TIMEOUT_SECONDS", "240")
    assert stream_timeout_seconds() == 240


def test_disconnect_cancels_pending_work_without_retry(caplog):
    visited = []

    async def source():
        try:
            await asyncio.sleep(10)
            visited.append("next_stage")
            yield "event: complete\ndata: {}\n\n"
        finally:
            visited.append("closed")

    async def run():
        stream = bounded_analysis_events(
            source(), request_id="disconnect-test", timeout_seconds=5
        )
        task = asyncio.create_task(stream.__anext__())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await stream.aclose()

    with caplog.at_level(logging.INFO, logger="sd_artifacts.stream"):
        asyncio.run(run())
    assert visited == ["closed"]
    assert "outcome=disconnected" in caplog.text


def test_deadline_does_not_reset_after_progress():
    async def source():
        while True:
            await asyncio.sleep(0.01)
            yield 'event: progress\ndata: {"node": "scanner"}\n\n'

    async def run():
        return [
            event
            async for event in bounded_analysis_events(
                source(),
                request_id="absolute-deadline",
                timeout_seconds=0.05,
            )
        ]

    events = asyncio.run(run())
    assert len(events) < 10
    detail = json.loads(events[-1].split("data: ", 1)[1])["detail"]
    assert detail["code"] == "analysis_timeout"
    assert detail["elapsed_ms"] < 250


def test_unexpected_stream_end_produces_terminal_error():
    async def source():
        if False:
            yield

    async def run():
        return [
            event
            async for event in bounded_analysis_events(
                source(),
                request_id="empty",
                timeout_seconds=1,
            )
        ]

    events = asyncio.run(run())
    assert len(events) == 1
    assert (
        json.loads(events[0].split("data: ", 1)[1])["detail"]["code"]
        == "analysis_error"
    )
