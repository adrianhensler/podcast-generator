import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import settings
from app.database import SessionLocal
from app.models import Project, StageLog
from app.services.llm_client import llm_stream
from app.services import research_generator, script_generator
from app.services.storage import write_artifact

logger = logging.getLogger(__name__)
router = APIRouter()


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _save_log(db, project, log):
    stage_log = StageLog(
        project_id=project.id,
        stage=log.stage,
        model=log.model,
        prompt_tokens=log.prompt_tokens,
        completion_tokens=log.completion_tokens,
        cost_usd=log.cost_usd,
        duration_ms=log.duration_ms,
        error=log.error,
        thinking=log.thinking,
    )
    db.add(stage_log)
    project.total_tokens += log.prompt_tokens + log.completion_tokens
    project.estimated_cost_usd += log.cost_usd


def _strip_thinking(raw: str) -> tuple[str, str | None]:
    """Return (clean_content, thinking_or_None). Handles unclosed <think> blocks."""
    # Close any unclosed <think> block (happens when max_tokens cuts off mid-think)
    if raw.count("<think>") > raw.count("</think>"):
        raw = raw + "</think>"
    thinking_blocks = re.findall(r"<think>(.*?)</think>", raw, flags=re.DOTALL)
    thinking = "\n\n---\n\n".join(b.strip() for b in thinking_blocks) or None
    content = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    return content, thinking


async def _run_stream(llm_gen, project_id: str, filename: str, db, project, terminal_status: str | None = None,
                      emit_expand_done: bool = False):
    """Shared tail: yield token SSE events, suppress <think> tokens, save to disk, commit.

    terminal_status: if set, project.status is updated and "done" is emitted.
    emit_expand_done: if True (and terminal_status is None), emits "expand_done" instead of "done"
                      to signal that more pipeline steps follow; the stream stays open.
                      When both are False/None, emits "done" (used by revision streams).
    """
    accumulated = []
    log = None
    in_thinking = False
    seen_content = False

    async for item in llm_gen:
        if isinstance(item, dict) and item.get("done"):
            log = item["log"]
            break
        elif isinstance(item, str):
            accumulated.append(item)

            # Parse mixed chunks by think-tag boundaries so one chunk can emit both modes.
            segments = re.split(r"(<think>|</think>)", item)
            for segment in segments:
                if not segment:
                    continue

                if segment == "<think>":
                    in_thinking = True
                    yield _sse({"type": "thinking"})
                    continue

                if segment == "</think>":
                    in_thinking = False
                    yield _sse({"type": "content_start"})
                    continue

                if in_thinking:
                    yield _sse({"type": "thinking_token", "text": segment})
                    continue

                if not seen_content and segment.strip():
                    seen_content = True
                    yield _sse({"type": "content_start"})
                yield _sse({"type": "token", "text": segment})

    raw = "".join(accumulated)
    content, thinking = _strip_thinking(raw)

    if thinking and log:
        log.thinking = thinking
    # Replace textarea with fully-cleaned content
    yield _sse({"type": "final_content", "text": content})

    await write_artifact(project_id, filename, content)

    if log:
        _save_log(db, project, log)
    if terminal_status:
        project.status = terminal_status
    db.commit()
    if emit_expand_done:
        yield _sse({"type": "expand_done"})
    else:
        yield _sse({"type": "done"})


async def _stream_brief(project_id: str):
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            yield _sse({"type": "error", "text": "Project not found"})
            return

        if project.status not in ("brief_pending", "brief_streaming"):
            yield _sse({"type": "error", "text": f"Unexpected status: {project.status}"})
            return

        project.status = "brief_streaming"
        db.commit()

        source_path = Path(settings.output_dir) / project_id / "normalized_sources.md"
        if not source_path.exists():
            project.status = "error"
            project.error_message = "Source content not found"
            db.commit()
            yield _sse({"type": "error", "text": "Source content not found"})
            return

        source_content = source_path.read_text(encoding="utf-8")
        system_prompt, user_prompt = research_generator.build_brief_prompt(
            project.url, source_content, project.tone, project.length,
            language=getattr(project, "language", "English"),
            flow_type=getattr(project, "flow_type", "explainer"),
        )

        gen = llm_stream(
            model=settings.model_outline,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=8192,
            stage_label="brief",
        )
        async for event in _run_stream(gen, project_id, "research_brief.md", db, project, "brief_ready"):
            yield event

    except Exception as e:
        logger.error("Brief stream error for %s: %s", project_id, e)
        try:
            proj = db.get(Project, project_id)
            if proj:
                proj.status = "error"
                proj.error_message = str(e)
                db.commit()
        except Exception:
            pass
        yield _sse({"type": "error", "text": str(e)})
    finally:
        db.close()


@router.get("/projects/{project_id}/stream/brief")
async def stream_brief(project_id: str):
    return StreamingResponse(
        _stream_brief(project_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_script(project_id: str):
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            yield _sse({"type": "error", "text": "Project not found"})
            return

        if project.status not in ("script_outline", "script_streaming"):
            yield _sse({"type": "error", "text": f"Unexpected status: {project.status}"})
            return

        project.status = "script_streaming"
        db.commit()

        brief_path = Path(settings.output_dir) / project_id / "research_brief.md"
        if not brief_path.exists():
            project.status = "error"
            project.error_message = "Research brief not found"
            db.commit()
            yield _sse({"type": "error", "text": "Research brief not found"})
            return
        brief = brief_path.read_text(encoding="utf-8")

        outline_path = Path(settings.output_dir) / project_id / "outline.json"
        if not outline_path.exists():
            project.status = "error"
            project.error_message = "Outline not found"
            db.commit()
            yield _sse({"type": "error", "text": "Outline not found"})
            return
        try:
            outline_dict = json.loads(outline_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            outline_dict = {}

        system_prompt, user_prompt = script_generator.build_expand_prompt(
            brief, outline_dict, project.num_speakers, project.tone, project.length,
            language=getattr(project, "language", "English"),
            flow_type=getattr(project, "flow_type", "explainer"),
        )

        gen = llm_stream(
            model=settings.model_expand,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.8,
            max_tokens=6000,
            stage_label="expand",
        )
        # Expand stream — save to script.md but don't set terminal status yet;
        # outro generation runs next before we mark script_ready.
        async for event in _run_stream(gen, project_id, "script.md", db, project,
                                       terminal_status=None, emit_expand_done=True):
            yield event

        # ── Outro generation ──────────────────────────────────────────────────
        # IMPORTANT: Run outro and commit status BEFORE any yields.
        # The Celery worker disconnects after receiving expand_done, so any
        # yield after that point raises GeneratorExit (BaseException — not caught
        # by except Exception). Status must be committed before we yield again.
        final_script = None
        try:
            script_path = Path(settings.output_dir) / project_id / "script.md"
            draft_script = script_path.read_text(encoding="utf-8")
            body, draft_outro = script_generator._split_script_body_and_draft_outro(draft_script)

            outro_text, outro_log = await script_generator.generate_outro(
                outline_dict,
                draft_outro,
                project.num_speakers,
                flow_type=getattr(project, "flow_type", "explainer"),
                language=getattr(project, "language", "English"),
            )

            final_script = body + "\n" + outro_text if body else outro_text
            await write_artifact(project_id, "script.md", final_script)
            _save_log(db, project, outro_log)
        except Exception as e:
            # Outro failure is non-fatal — the expand script is still valid.
            logger.warning("Outro generation failed for %s, keeping expand script: %s", project_id, e)

        # Commit script_ready before any yields — a disconnected client must not
        # prevent this from running.
        project.status = "script_ready"
        db.commit()

        # Remaining yields are for the browser UI only; Celery worker has already
        # disconnected. GeneratorExit here is harmless — status is already set.
        yield _sse({"type": "outro_start"})
        if final_script is not None:
            yield _sse({"type": "final_content", "text": final_script})
        yield _sse({"type": "done"})

    except Exception as e:
        logger.error("Script stream error for %s: %s", project_id, e)
        try:
            proj = db.get(Project, project_id)
            if proj:
                proj.status = "error"
                proj.error_message = str(e)
                db.commit()
        except Exception:
            pass
        yield _sse({"type": "error", "text": str(e)})
    finally:
        db.close()


@router.get("/projects/{project_id}/stream/script")
async def stream_script(project_id: str):
    return StreamingResponse(
        _stream_script(project_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_revise_brief(project_id: str, instruction: str):
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            yield _sse({"type": "error", "text": "Project not found"})
            return

        brief_path = Path(settings.output_dir) / project_id / "research_brief.md"
        current_brief = brief_path.read_text(encoding="utf-8") if brief_path.exists() else ""

        system_prompt = (
            "You are a research analyst revising a research brief. "
            "Apply the user's instruction to improve the brief. Maintain factual accuracy. "
            "Return only the revised brief text — no commentary, no headers."
        )
        user_prompt = (
            f"Current brief:\n{current_brief}\n\n"
            f"Revision instruction: {instruction}\n\n"
            "Return the full revised brief."
        )

        gen = llm_stream(
            model=settings.model_outline,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.5,
            max_tokens=8192,
            stage_label="brief_revision",
        )
        async for event in _run_stream(gen, project_id, "research_brief.md", db, project):
            yield event

    except Exception as e:
        logger.error("Brief revision stream error for %s: %s", project_id, e)
        yield _sse({"type": "error", "text": str(e)})
    finally:
        db.close()


@router.post("/projects/{project_id}/stream/revise-brief")
async def stream_revise_brief(project_id: str, request: Request):
    body = await request.json()
    instruction = body.get("instruction", "").strip()
    if not instruction:
        return JSONResponse({"error": "instruction required"}, status_code=400)
    return StreamingResponse(
        _stream_revise_brief(project_id, instruction),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_revise_script(project_id: str, instruction: str):
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            yield _sse({"type": "error", "text": "Project not found"})
            return

        script_path = Path(settings.output_dir) / project_id / "script.md"
        current_script = script_path.read_text(encoding="utf-8") if script_path.exists() else ""

        system_prompt = (
            "You are a podcast scriptwriter revising a script. "
            "Apply the user's instruction to improve the script. Maintain the Host A:/Host B: format. "
            "Return only the revised script — no commentary."
        )
        user_prompt = (
            f"Current script:\n{current_script}\n\n"
            f"Revision instruction: {instruction}\n\n"
            "Return the full revised script."
        )

        gen = llm_stream(
            model=settings.model_expand,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=6000,
            stage_label="script_revision",
        )
        async for event in _run_stream(gen, project_id, "script.md", db, project):
            yield event

    except Exception as e:
        logger.error("Script revision stream error for %s: %s", project_id, e)
        yield _sse({"type": "error", "text": str(e)})
    finally:
        db.close()


@router.post("/projects/{project_id}/stream/revise-script")
async def stream_revise_script(project_id: str, request: Request):
    body = await request.json()
    instruction = body.get("instruction", "").strip()
    if not instruction:
        return JSONResponse({"error": "instruction required"}, status_code=400)
    return StreamingResponse(
        _stream_revise_script(project_id, instruction),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
