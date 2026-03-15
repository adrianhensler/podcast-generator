import logging
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.models import Project, StageLog
from app.services import source_ingest, research_generator, script_generator
from app.services.source_ingest import IngestionError
from app.services.script_generator import ScriptParseError

logger = logging.getLogger(__name__)
router = APIRouter()


def _title_from_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/").split("/")[-1] or parsed.netloc
    return f"{parsed.netloc} — {path}" if path and path != parsed.netloc else parsed.netloc


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    from app.main import templates
    return templates.TemplateResponse(request, "index.html")


@router.post("/projects")
async def create_project(
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    num_speakers: int = Form(2),
    tone: str = Form("neutral"),
    length: str = Form("medium"),
    use_tavily: bool = Form(False),
    host_a_voice: str = Form("Wise_Woman"),
    host_b_voice: str = Form("Deep_Voice_Man"),
    db: Session = Depends(get_db),
):
    project = Project(
        url=url,
        title=_title_from_url(url),
        num_speakers=num_speakers,
        tone=tone,
        length=length,
        use_tavily=use_tavily,
        host_a_voice=host_a_voice,
        host_b_voice=host_b_voice,
        status="pending",
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    background_tasks.add_task(run_ingest_only, project.id)

    response = RedirectResponse(url=f"/projects/{project.id}", status_code=303)
    response.headers["HX-Trigger"] = "projectCreated"
    return response


@router.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_page(project_id: str, request: Request, db: Session = Depends(get_db)):
    from app.main import templates
    project = db.get(Project, project_id)
    if not project:
        return HTMLResponse("Project not found", status_code=404)
    return templates.TemplateResponse(request, "project.html", {"project": project})


@router.get("/projects", response_class=HTMLResponse)
async def project_list(request: Request, page: int = 1, db: Session = Depends(get_db)):
    from app.main import templates
    page_size = 10
    offset = (page - 1) * page_size
    projects = db.query(Project).order_by(Project.created_at.desc()).offset(offset).limit(page_size + 1).all()
    has_next = len(projects) > page_size
    projects = projects[:page_size]
    return templates.TemplateResponse(
        request, "partials/project_list.html",
        {"projects": projects, "page": page, "has_next": has_next},
    )


@router.get("/projects/{project_id}/status/json")
async def project_status_json(project_id: str, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse({"status": project.status, "error": project.error_message})


@router.get("/projects/{project_id}/status/audio", response_class=HTMLResponse)
async def project_status_audio(project_id: str, request: Request, db: Session = Depends(get_db)):
    from app.main import templates
    project = db.get(Project, project_id)
    if not project:
        return HTMLResponse("Not found", status_code=404)
    return templates.TemplateResponse(
        request, "partials/audio_status.html",
        {"project": project},
    )


@router.post("/projects/{project_id}/generate-script")
async def generate_script(
    project_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project:
        return HTMLResponse("Not found", status_code=404)
    project.status = "scripting"
    project.error_message = None
    db.commit()
    background_tasks.add_task(run_script_outline, project_id)
    resp = HTMLResponse("")
    resp.headers["HX-Refresh"] = "true"
    return resp


@router.post("/projects/{project_id}/render")
async def render_audio(
    project_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    from app.main import templates

    project = db.get(Project, project_id)
    if not project:
        return HTMLResponse("Not found", status_code=404)

    # Update voice settings from form data
    form = await request.form()
    host_a_voice = form.get("host_a_voice", project.host_a_voice)
    host_b_voice = form.get("host_b_voice", project.host_b_voice)
    tts_model = form.get("tts_model", "turbo")
    project.host_a_voice = host_a_voice
    project.host_b_voice = host_b_voice
    project.status = "rendering"
    project.error_message = None
    db.commit()

    background_tasks.add_task(run_tts_render, project_id, tts_model)
    resp = HTMLResponse("")
    resp.headers["HX-Refresh"] = "true"
    return resp


# --- Background task functions (each creates its own DB session) ---

async def run_ingest_only(project_id: str):
    """Ingest URL → set brief_pending. Browser then opens SSE stream for brief."""
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            return

        project.status = "ingesting"
        db.commit()
        try:
            source_content = await source_ingest.ingest(
                project_id, project.url, project.use_tavily
            )
        except IngestionError as e:
            _set_error(db, project, str(e))
            return
        except Exception as e:
            _set_error(db, project, f"Ingestion error: {e}")
            return

        project.status = "brief_pending"
        db.commit()
        logger.info("Project %s: brief_pending", project_id)

    finally:
        db.close()


async def run_script_outline(project_id: str):
    """Generate outline (sync/fast) → write outline.json → set script_outline."""
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            return

        from pathlib import Path
        from app.config import settings as app_settings

        brief_path = Path(app_settings.output_dir) / project_id / "research_brief.md"
        if not brief_path.exists():
            _set_error(db, project, "No research brief found. Please regenerate from step 1.")
            return
        brief = brief_path.read_text(encoding="utf-8")

        target_words = script_generator.LENGTH_TARGETS.get(project.length, 1500)
        try:
            outline_json, log = await script_generator._generate_outline(
                brief, project.num_speakers, project.tone, target_words
            )
        except Exception as e:
            _set_error(db, project, f"Outline generation error: {e}")
            return

        _save_log(db, project, log)

        import json as _json
        outline_path = Path(app_settings.output_dir) / project_id / "outline.json"
        outline_path.write_text(outline_json, encoding="utf-8")

        project.status = "script_outline"
        db.commit()
        logger.info("Project %s: script_outline", project_id)

    finally:
        db.close()


async def run_tts_render(project_id: str, tts_model: str = "turbo"):
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            return

        from app.services.tts_renderer import render
        from app.services.script_generator import parse_script_lines, ScriptParseError
        from app.models import StageLog
        from pathlib import Path
        import time as _time
        from app.config import settings as app_settings

        script_path = Path(app_settings.output_dir) / project_id / "script.md"
        if not script_path.exists():
            _set_error(db, project, "No script found. Please generate a script first.")
            return

        script_text = script_path.read_text(encoding="utf-8")

        try:
            lines = parse_script_lines(script_text)
        except ScriptParseError as e:
            _set_error(db, project, str(e))
            return

        model_cfg = app_settings.tts_models.get(tts_model, app_settings.tts_models["turbo"])
        api_url = model_cfg["url"]
        cost_per_m = model_cfg["cost_per_m_chars"]

        t0 = _time.monotonic()
        try:
            audio_path, total_chars = await render(
                project_id, lines, project.host_a_voice, project.host_b_voice, api_url
            )
        except Exception as e:
            _set_error(db, project, f"Audio render error: {e}")
            return
        duration_ms = int((_time.monotonic() - t0) * 1000)

        cost_usd = total_chars * cost_per_m / 1_000_000
        tts_log = StageLog(
            project_id=project_id,
            stage="tts",
            model=f"speech-02-{tts_model}",
            prompt_tokens=total_chars,
            completion_tokens=0,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
        )
        db.add(tts_log)
        project.estimated_cost_usd += cost_usd
        project.status = "done"
        db.commit()
        logger.info("Project %s: done → %s (%d chars, $%.4f)", project_id, audio_path, total_chars, cost_usd)

    finally:
        db.close()


def _save_log(db, project, log):
    from app.models import StageLog
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


def _set_error(db, project, message: str):
    project.status = "error"
    project.error_message = message
    db.commit()
    logger.error("Project %s error: %s", project.id, message)
