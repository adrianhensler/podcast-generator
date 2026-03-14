import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import init_db
from app.routers import projects, artifacts, audio, stream

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB
    init_db()
    # Ensure output dir exists
    Path(settings.output_dir).mkdir(parents=True, exist_ok=True)
    # Pre-generate silence file
    from app.services.tts_renderer import get_silence_path
    try:
        get_silence_path()
    except Exception as e:
        logging.getLogger(__name__).warning("Could not pre-generate silence file: %s", e)
    yield


app = FastAPI(title="Research Podcast Studio", lifespan=lifespan)

# Static files
static_dir = BASE_DIR.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Output files (served directly)
output_dir = Path(settings.output_dir)
output_dir.mkdir(parents=True, exist_ok=True)
app.mount("/output", StaticFiles(directory=str(output_dir)), name="output")

# Templates
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Routers
app.include_router(projects.router)
app.include_router(artifacts.router)
app.include_router(audio.router)
app.include_router(stream.router)
