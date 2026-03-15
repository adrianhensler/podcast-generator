import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.llm_client as llm_client_module
from app.models import Project
from app.routers import projects as projects_router
from app.routers import stream as stream_router


@pytest.fixture
def isolated_runtime(monkeypatch, tmp_path, test_engine):
    """Point output + SessionLocal bindings at per-test resources."""
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    from sqlalchemy.orm import sessionmaker

    test_session_local = sessionmaker(bind=test_engine)
    monkeypatch.setattr("app.config.settings.output_dir", str(output_dir))
    monkeypatch.setattr(stream_router, "SessionLocal", test_session_local)
    monkeypatch.setattr(projects_router, "SessionLocal", test_session_local)

    return output_dir


def _extract_sse_payloads(response):
    payloads = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            payloads.append(json.loads(line[len("data: ") :]))
    return payloads


@pytest.mark.asyncio
async def test_projects_creation_and_transition_into_streamable_states(
    client, db_session, isolated_runtime, monkeypatch
):
    async def fake_ingest(_project_id, _url, _use_tavily):
        return "normalized source"

    monkeypatch.setattr("app.services.source_ingest.ingest", fake_ingest)

    with pytest.MonkeyPatch.context() as m:
        m.setattr("app.routers.projects.run_ingest_only", AsyncMock())
        create_resp = client.post(
            "/projects",
            data={"url": "https://example.com/story", "num_speakers": "2", "tone": "neutral", "length": "short"},
            follow_redirects=False,
        )

    assert create_resp.status_code == 303
    project_id = create_resp.headers["location"].split("/")[-1]

    await projects_router.run_ingest_only(project_id)

    project = db_session.get(Project, project_id)
    assert project.status == "brief_pending"

    brief_path = isolated_runtime / project_id / "research_brief.md"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text("brief content", encoding="utf-8")

    fake_log = SimpleNamespace(
        stage="outline",
        model="test-model",
        prompt_tokens=10,
        completion_tokens=20,
        cost_usd=0.01,
        duration_ms=50,
        error=None,
        thinking=None,
    )

    async def fake_generate_outline(_brief, _num_speakers, _tone, _target_words):
        return '{"sections": ["Intro"]}', fake_log

    monkeypatch.setattr("app.services.script_generator._generate_outline", fake_generate_outline)

    await projects_router.run_script_outline(project_id)

    db_session.expire_all()
    project = db_session.get(Project, project_id)
    assert project.status == "script_outline"
    assert (isolated_runtime / project_id / "outline.json").exists()


@pytest.mark.asyncio
async def test_stream_brief_and_script_emit_expected_sse_and_write_artifacts(
    client, db_session, isolated_runtime, monkeypatch
):
    project = Project(
        url="https://example.com/episode",
        title="example",
        status="brief_pending",
        num_speakers=2,
        tone="neutral",
        length="short",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    project_dir = isolated_runtime / project.id
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "normalized_sources.md").write_text("normalized", encoding="utf-8")

    async def fake_llm_stream(*_args, **_kwargs):
        yield "hello "
        yield "world"
        yield {
            "done": True,
            "log": SimpleNamespace(
                stage="brief",
                model="test-model",
                prompt_tokens=2,
                completion_tokens=3,
                cost_usd=0.001,
                duration_ms=5,
                error=None,
                thinking=None,
            ),
        }

    monkeypatch.setattr(llm_client_module, "llm_stream", fake_llm_stream)
    monkeypatch.setattr(stream_router, "llm_stream", fake_llm_stream)
    monkeypatch.setattr("app.services.research_generator.build_brief_prompt", lambda *_: ("sys", "user"))

    brief_resp = client.get(f"/projects/{project.id}/stream/brief")
    brief_events = _extract_sse_payloads(brief_resp)

    assert {"type": "token", "text": "hello "} in brief_events
    assert {"type": "token", "text": "world"} in brief_events
    assert {"type": "final_content", "text": "hello world"} in brief_events
    assert brief_events[-1] == {"type": "done"}

    db_session.expire_all()
    project = db_session.get(Project, project.id)
    assert project.status == "brief_ready"
    assert (project_dir / "research_brief.md").read_text(encoding="utf-8") == "hello world"

    (project_dir / "outline.json").write_text('{"sections": ["a"]}', encoding="utf-8")
    project.status = "script_outline"
    db_session.commit()

    async def fake_llm_stream_script(*_args, **_kwargs):
        yield "Host A: hi\n"
        yield "Host B: there"
        yield {
            "done": True,
            "log": SimpleNamespace(
                stage="expand",
                model="test-model",
                prompt_tokens=4,
                completion_tokens=5,
                cost_usd=0.002,
                duration_ms=8,
                error=None,
                thinking=None,
            ),
        }

    monkeypatch.setattr(llm_client_module, "llm_stream", fake_llm_stream_script)
    monkeypatch.setattr(stream_router, "llm_stream", fake_llm_stream_script)
    monkeypatch.setattr("app.services.script_generator.build_expand_prompt", lambda *_: ("sys", "user"))

    script_resp = client.get(f"/projects/{project.id}/stream/script")
    script_events = _extract_sse_payloads(script_resp)

    assert {"type": "token", "text": "Host A: hi\n"} in script_events
    assert {"type": "token", "text": "Host B: there"} in script_events
    assert {"type": "final_content", "text": "Host A: hi\nHost B: there"} in script_events
    assert script_events[-1] == {"type": "done"}

    db_session.expire_all()
    project = db_session.get(Project, project.id)
    assert project.status == "script_ready"
    assert (project_dir / "script.md").read_text(encoding="utf-8") == "Host A: hi\nHost B: there"


@pytest.mark.asyncio
async def test_stream_generator_error_sets_project_status_error(
    client, db_session, isolated_runtime, monkeypatch
):
    project = Project(url="https://example.com/error", title="err", status="brief_pending")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    project_dir = isolated_runtime / project.id
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "normalized_sources.md").write_text("normalized", encoding="utf-8")

    async def failing_llm_stream(*_args, **_kwargs):
        yield "partial"
        raise RuntimeError("boom")

    monkeypatch.setattr(llm_client_module, "llm_stream", failing_llm_stream)
    monkeypatch.setattr(stream_router, "llm_stream", failing_llm_stream)
    monkeypatch.setattr("app.services.research_generator.build_brief_prompt", lambda *_: ("sys", "user"))

    resp = client.get(f"/projects/{project.id}/stream/brief")
    events = _extract_sse_payloads(resp)

    assert {"type": "token", "text": "partial"} in events
    assert events[-1] == {"type": "error", "text": "boom"}

    db_session.expire_all()
    project = db_session.get(Project, project.id)
    assert project.status == "error"
    assert project.error_message == "boom"
