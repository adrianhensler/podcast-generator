from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.models import Artifact, Project
from app.routers.projects import run_tts_render


@pytest.mark.asyncio
async def test_run_tts_render_reads_script_from_filesystem_not_artifact_table(db_session, tmp_path):
    project_id = "proj-filesystem-script"
    project = Project(
        id=project_id,
        url="https://example.com/source",
        title="example",
        status="rendering",
    )
    db_session.add(project)
    db_session.add(
        Artifact(
            project_id=project_id,
            artifact_type="script",
            file_path="nonexistent-script.md",
        )
    )
    db_session.commit()

    output_dir_before = settings.output_dir
    settings.output_dir = str(tmp_path)
    try:
        project_dir = tmp_path / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        script_path = project_dir / "script.md"
        script_path.write_text("Host A: hello there\nHost B: general kenobi\n", encoding="utf-8")

        with (
            patch("app.routers.projects.SessionLocal", return_value=db_session),
            patch("app.services.tts_renderer.render", new=AsyncMock(return_value=str(project_dir / "audio.mp3"))) as render_mock,
        ):
            await run_tts_render(project_id)

        render_mock.assert_awaited_once()
    finally:
        settings.output_dir = output_dir_before
