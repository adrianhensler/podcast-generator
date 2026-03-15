import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.routers.stream import _run_stream


class DummyDB:
    def __init__(self):
        self.committed = False

    def add(self, _obj):
        pass

    def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_run_stream_splits_think_and_visible_tokens_in_one_chunk():
    async def gen():
        yield "<think>abc</think>visible"
        yield {
            "done": True,
            "log": SimpleNamespace(
                stage="brief",
                model="test",
                prompt_tokens=1,
                completion_tokens=1,
                cost_usd=0.0,
                duration_ms=1,
                error=None,
                thinking=None,
            ),
        }

    db = DummyDB()
    project = SimpleNamespace(id="p1", total_tokens=0, estimated_cost_usd=0.0, status="x")

    events = []
    with patch("app.routers.stream.write_artifact", new=AsyncMock()) as write_artifact:
        async for event in _run_stream(gen(), "p1", "script.md", db, project):
            payload = json.loads(event.removeprefix("data: ").strip())
            events.append(payload)

    assert events[:5] == [
        {"type": "thinking"},
        {"type": "thinking_token", "text": "abc"},
        {"type": "content_start"},   # emitted on </think>
        {"type": "content_start"},   # emitted on first visible segment
        {"type": "token", "text": "visible"},
    ]
    assert events[-2] == {"type": "final_content", "text": "visible"}
    assert events[-1] == {"type": "done"}
    write_artifact.assert_awaited_once_with("p1", "script.md", "visible")


@pytest.mark.asyncio
async def test_run_stream_tracks_thinking_across_chunks_and_emits_visible_tokens():
    async def gen():
        yield "pre<think>x"
        yield "y</think>post"
        yield {
            "done": True,
            "log": SimpleNamespace(
                stage="brief",
                model="test",
                prompt_tokens=1,
                completion_tokens=1,
                cost_usd=0.0,
                duration_ms=1,
                error=None,
                thinking=None,
            ),
        }

    db = DummyDB()
    project = SimpleNamespace(id="p2", total_tokens=0, estimated_cost_usd=0.0, status="x")

    events = []
    with patch("app.routers.stream.write_artifact", new=AsyncMock()):
        async for event in _run_stream(gen(), "p2", "script.md", db, project):
            payload = json.loads(event.removeprefix("data: ").strip())
            events.append(payload)

    assert events[:7] == [
        {"type": "content_start"},
        {"type": "token", "text": "pre"},
        {"type": "thinking"},
        {"type": "thinking_token", "text": "x"},
        {"type": "thinking_token", "text": "y"},
        {"type": "content_start"},   # emitted on </think>
        {"type": "token", "text": "post"},
    ]
    assert events[-2] == {"type": "final_content", "text": "prepost"}
    assert events[-1] == {"type": "done"}
