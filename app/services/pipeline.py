import asyncio
import json
import logging
import time
from pathlib import Path

import httpx

from app.config import settings
from app.database import SessionLocal
from app.models import Project, StageLog
from app.services import research_generator, script_generator, tts_renderer
from app.services.script_generator import ScriptParseError
from app.services.storage import write_artifact

logger = logging.getLogger(__name__)

_INGEST_POLL_INTERVAL = 2.0
_INGEST_TIMEOUT = 300  # 5 min max wait for ingest


async def _post_callback(callback_url: str | None, stage: str, status: str, **kwargs) -> None:
    """POST a stage webhook. Logs on failure — never raises, never blocks the pipeline."""
    if not callback_url:
        return
    payload = {"stage": stage, "status": status, **kwargs}
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(callback_url, json=payload)
            if resp.status_code < 300:
                return
            logger.warning("Callback %s returned %d (attempt %d)", callback_url, resp.status_code, attempt + 1)
        except Exception as exc:
            logger.warning("Callback %s failed (attempt %d): %s", callback_url, attempt + 1, exc)
        if attempt < 2:
            await asyncio.sleep(2.0)


def _save_log(db, project, log) -> None:
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
        truncated=log.truncated,
    )
    db.add(stage_log)
    project.total_tokens += log.prompt_tokens + log.completion_tokens
    project.estimated_cost_usd += log.cost_usd


async def run_pipeline(project_id: str, callback_url: str | None = None) -> None:
    """Self-driving pipeline: ingest wait → brief → outline → expand → outro → editor → TTS.

    Posts webhook callbacks at brief_ready, script_ready, pipeline_done, and error.
    Designed to run as a FastAPI BackgroundTask.
    """
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            logger.error("run_pipeline: project %s not found", project_id)
            return

        # 1. Wait for ingest to finish (brief_pending or error)
        deadline = time.monotonic() + _INGEST_TIMEOUT
        while project.status not in ("brief_pending", "error") and time.monotonic() < deadline:
            await asyncio.sleep(_INGEST_POLL_INTERVAL)
            db.refresh(project)

        if project.status == "error":
            await _post_callback(callback_url, "error", "error",
                                  project_id=project_id,
                                  message=project.error_message or "Ingest failed")
            return

        if project.status != "brief_pending":
            msg = f"Ingest timed out after {_INGEST_TIMEOUT}s (status={project.status})"
            project.status = "error"
            project.error_message = msg
            db.commit()
            await _post_callback(callback_url, "error", "error", project_id=project_id, message=msg)
            return

        # 2. Read source content (+ Tavily results if present)
        source_path = Path(settings.output_dir) / project_id / "normalized_sources.md"
        source_content = source_path.read_text(encoding="utf-8")

        tavily_path = Path(settings.output_dir) / project_id / "tavily_results.md"
        tavily_content = ""
        if tavily_path.exists():
            tavily_content = tavily_path.read_text(encoding="utf-8")
            if tavily_content.strip():
                source_content = (
                    source_content
                    + "\n\n---\n\n## Additional Context (Tavily)\n\n"
                    + tavily_content
                )

        # 3. Generate research brief
        project.status = "brief_streaming"
        db.commit()

        brief, brief_log = await research_generator.generate(
            project_id=project_id,
            url=project.url,
            source_content=source_content,
            tone=project.tone,
            length=project.length,
            language=project.language,
            flow_type=project.flow_type,
        )
        _save_log(db, project, brief_log)
        project.status = "brief_ready"
        db.commit()
        await _post_callback(callback_url, "brief", "brief_ready",
                              project_id=project_id, cost_usd=project.estimated_cost_usd)

        # 4. Generate outline
        project.status = "script_outline"
        db.commit()

        target_words = script_generator.LENGTH_TARGETS.get(project.length, 1500)
        outline_json, outline_log = await script_generator._generate_outline(
            brief, project.num_speakers, project.tone, target_words,
            language=project.language, flow_type=project.flow_type,
        )
        _save_log(db, project, outline_log)
        # Write outline.json for UI compatibility (script revision endpoint reads it)
        outline_path = Path(settings.output_dir) / project_id / "outline.json"
        outline_path.write_text(outline_json, encoding="utf-8")
        db.commit()

        try:
            outline_dict = json.loads(outline_json)
        except (json.JSONDecodeError, TypeError):
            outline_dict = {}

        # 5. Expand to script
        project.status = "script_streaming"
        db.commit()

        script_text, expand_log = await script_generator._expand_to_script(
            brief, outline_json, project.num_speakers, project.tone, project.length,
            language=project.language, flow_type=project.flow_type,
        )
        _save_log(db, project, expand_log)
        await write_artifact(project_id, "script.md", script_text)
        db.commit()

        # 6. Generate outro
        body_text, draft_outro = script_generator._split_script_body_and_draft_outro(script_text)
        try:
            outro_text, outro_log = await script_generator.generate_outro(
                outline_dict, draft_outro, project.num_speakers,
                flow_type=project.flow_type, language=project.language,
            )
            final_script = (body_text + "\n" + outro_text) if body_text else outro_text
            await write_artifact(project_id, "script.md", final_script)
            _save_log(db, project, outro_log)
            db.commit()
        except Exception as e:
            logger.warning("Outro failed for %s, keeping expand script: %s", project_id, e)
            final_script = script_text

        # 7. Editor pass
        try:
            edited_script, editor_log = await script_generator.editor_pass(
                project_id=project_id,
                script=final_script,
                brief=brief,
                tavily_content=tavily_content,
                flow_type=project.flow_type,
                length=project.length,
            )
            if editor_log.model != "skipped":
                try:
                    script_generator.parse_script_lines(edited_script)
                    await write_artifact(project_id, "script.md", edited_script)
                    final_script = edited_script
                    _save_log(db, project, editor_log)
                    db.commit()
                except ScriptParseError as e:
                    logger.warning("Editor pass produced invalid format for %s: %s — keeping pre-edit script",
                                   project_id, e)
        except Exception as e:
            logger.warning("Editor pass failed for %s: %s", project_id, e)

        project.status = "script_ready"
        db.commit()
        await _post_callback(callback_url, "script", "script_ready",
                              project_id=project_id, cost_usd=project.estimated_cost_usd)

        # 8. TTS rendering
        project.status = "rendering"
        db.commit()

        script_lines = script_generator.parse_script_lines(final_script)

        model_cfg = settings.tts_models.get("turbo", settings.tts_models["turbo"])
        cost_per_m = model_cfg["cost_per_m_chars"]

        t0 = time.monotonic()
        audio_path, total_chars = await tts_renderer.render(
            project_id=project_id,
            script_lines=script_lines,
            host_a_voice=project.host_a_voice,
            host_b_voice=project.host_b_voice,
            api_url=model_cfg["url"],
            language=project.language,
        )
        duration_ms = int((time.monotonic() - t0) * 1000)

        tts_cost_usd = total_chars * cost_per_m / 1_000_000
        tts_log = StageLog(
            project_id=project_id,
            stage="tts",
            model="speech-02-turbo",
            prompt_tokens=total_chars,
            completion_tokens=0,
            cost_usd=tts_cost_usd,
            duration_ms=duration_ms,
        )
        db.add(tts_log)
        project.estimated_cost_usd += tts_cost_usd
        project.status = "done"
        db.commit()
        logger.info("Pipeline done for %s → %s (%d chars, $%.4f)",
                    project_id, audio_path, total_chars, project.estimated_cost_usd)

        await _post_callback(callback_url, "tts", "pipeline_done",
                              project_id=project_id,
                              cost_usd=project.estimated_cost_usd,
                              total_tokens=project.total_tokens)

    except Exception as e:
        logger.error("Pipeline error for %s: %s", project_id, e, exc_info=True)
        try:
            proj = db.get(Project, project_id)
            if proj and proj.status != "done":
                proj.status = "error"
                proj.error_message = str(e)
                db.commit()
        except Exception:
            pass
        await _post_callback(callback_url, "error", "error",
                              project_id=project_id, message=str(e))
    finally:
        db.close()
