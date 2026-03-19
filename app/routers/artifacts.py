import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Project

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_TYPES = {"normalized_sources", "research_brief", "script", "outline", "tavily_results"}


def _get_artifact_path(project_id: str, artifact_type: str) -> Path:
    """Resolve artifact path from the filesystem artifact naming convention."""
    filename_map = {
        "normalized_sources": "normalized_sources.md",
        "research_brief": "research_brief.md",
        "script": "script.md",
        "outline": "outline.json",
        "tavily_results": "tavily_results.md",
    }
    filename = filename_map.get(artifact_type)
    if not filename:
        raise HTTPException(status_code=400, detail=f"Unknown artifact type: {artifact_type}")
    return Path(settings.output_dir) / project_id / filename


@router.get("/projects/{project_id}/artifacts/{artifact_type}")
async def get_artifact(project_id: str, artifact_type: str, db: Session = Depends(get_db)):
    if artifact_type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail="Invalid artifact type")

    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    path = _get_artifact_path(project_id, artifact_type)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{artifact_type} not found")

    content = path.read_text(encoding="utf-8")
    return JSONResponse({"content": content})


@router.put("/projects/{project_id}/artifacts/{artifact_type}")
async def update_artifact(
    project_id: str,
    artifact_type: str,
    request: Request,
    db: Session = Depends(get_db),
):
    if artifact_type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail="Invalid artifact type")

    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    ct = request.headers.get("content-type", "")
    if "application/json" in ct:
        data = await request.json()
        content = data.get("content", "")
    else:
        form = await request.form()
        content = form.get("content", "")
    path = _get_artifact_path(project_id, artifact_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    logger.info("Updated artifact %s for project %s", artifact_type, project_id)
    return JSONResponse({"status": "saved"})


@router.get("/projects/{project_id}/artifacts/{artifact_type}/download")
async def download_artifact(project_id: str, artifact_type: str, db: Session = Depends(get_db)):
    if artifact_type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail="Invalid artifact type")

    path = _get_artifact_path(project_id, artifact_type)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(str(path), filename=path.name)
