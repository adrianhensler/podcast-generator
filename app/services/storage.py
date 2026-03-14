import aiofiles
from pathlib import Path
from app.config import settings


def project_dir(project_id: str) -> Path:
    p = Path(settings.output_dir) / project_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def segments_dir(project_id: str) -> Path:
    p = project_dir(project_id) / "segments"
    p.mkdir(parents=True, exist_ok=True)
    return p


async def write_artifact(project_id: str, filename: str, content: str) -> str:
    """Write text content to output/<project_id>/<filename>. Returns relative path."""
    path = project_dir(project_id) / filename
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(content)
    return str(path)


async def read_artifact(file_path: str) -> str:
    async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
        return await f.read()


async def write_bytes(path: Path, data: bytes) -> None:
    async with aiofiles.open(path, "wb") as f:
        await f.write(data)
