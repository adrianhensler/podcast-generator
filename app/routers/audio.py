import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Project

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/projects/{project_id}/audio")
async def serve_audio(project_id: str, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    audio_path = Path(settings.output_dir) / project_id / "audio.mp3"
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio not yet rendered")

    return FileResponse(
        str(audio_path),
        media_type="audio/mpeg",
        filename=f"podcast_{project_id[:8]}.mp3",
    )
