import asyncio
import logging
import subprocess
import time
from pathlib import Path

import httpx

from app.config import settings
from app.services.script_generator import ScriptLine
from app.services.text_normalizer import normalize_for_speech
from app.services.storage import project_dir, segments_dir, write_bytes

logger = logging.getLogger(__name__)

REPLICATE_API_URL_TURBO = "https://api.replicate.com/v1/models/minimax/speech-02-turbo/predictions"
MAX_POLL_TIMEOUT = 120
SILENCE_DURATION_MS = 300

# Pre-generated silence file path (global, created at startup)
_SILENCE_PATH: Path | None = None


class TTSError(Exception):
    pass


def get_silence_path() -> Path:
    global _SILENCE_PATH
    if _SILENCE_PATH is None or not _SILENCE_PATH.exists():
        _SILENCE_PATH = _generate_silence()
    return _SILENCE_PATH


def _generate_silence() -> Path:
    """Generate a short silence MP3 using ffmpeg."""
    path = Path(settings.output_dir) / "silence_300ms.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", f"anullsrc=r=24000:cl=mono",
                "-t", f"{SILENCE_DURATION_MS / 1000}",
                "-ar", "24000", "-ac", "1", "-b:a", "96k",
                str(path),
            ],
            capture_output=True,
            check=True,
        )
        logger.info("Generated silence file: %s", path)
    return path


async def render(
    project_id: str,
    script_lines: list[ScriptLine],
    host_a_voice: str = "Wise_Woman",
    host_b_voice: str = "Deep_Voice_Man",
    api_url: str = REPLICATE_API_URL_TURBO,
    language: str = "English",
) -> tuple[str, int]:
    """Render all script lines to audio segments, stitch, return (path, total_chars)."""
    seg_dir = segments_dir(project_id)
    silence_path = get_silence_path()

    semaphore = asyncio.Semaphore(settings.replicate_concurrency)

    async def render_line(idx: int, line: ScriptLine) -> tuple[Path, int]:
        voice = host_a_voice if line.speaker == "Host A" else host_b_voice
        normalized_text = normalize_for_speech(line.text)
        seg_path = seg_dir / f"seg_{idx:04d}.mp3"
        async with semaphore:
            await _render_segment(normalized_text, voice, seg_path, api_url, language)
        return seg_path, len(normalized_text)

    tasks = [render_line(i, line) for i, line in enumerate(script_lines)]
    results = await asyncio.gather(*tasks)
    segment_paths = [r[0] for r in results]
    total_chars = sum(r[1] for r in results)

    # Build concat list: seg, silence, seg, silence, ...
    audio_mp3 = project_dir(project_id) / "audio.mp3"
    _stitch_segments(list(segment_paths), silence_path, audio_mp3)

    logger.info("Rendered %d segments → %s", len(segment_paths), audio_mp3)
    return str(audio_mp3), total_chars


async def _render_segment(text: str, voice_id: str, output_path: Path, api_url: str = REPLICATE_API_URL_TURBO, language: str = "English") -> None:
    headers = {
        "Authorization": f"Bearer {settings.replicate_api_token}",
        "Content-Type": "application/json",
        "Prefer": "wait",
    }
    payload = {
        "input": {
            "text": text,
            "voice_id": voice_id,
            "sample_rate": 24000,
            "channel": "mono",
            "bitrate": 128000,
            "audio_format": "mp3",
            "language_boost": language,
            "emotion": "auto",
        }
    }

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(api_url, headers=headers, json=payload)

    if resp.status_code == 201:
        # Sync result with Prefer: wait
        prediction = resp.json()
    elif resp.status_code == 202:
        # Async — fall back to polling
        prediction = resp.json()
        prediction = await _poll_prediction(prediction["urls"]["get"])
    else:
        raise TTSError(f"Replicate returned {resp.status_code}: {resp.text[:300]}")

    audio_url = _extract_output(prediction)
    await _download_audio(audio_url, output_path)


def _extract_output(prediction: dict) -> str:
    status = prediction.get("status")
    if status == "failed":
        raise TTSError(f"Prediction failed: {prediction.get('error', 'unknown')}")
    output = prediction.get("output")
    if not output:
        raise TTSError(f"No output in prediction: {prediction}")
    # output may be a string URL or a list
    if isinstance(output, list):
        return output[0]
    return output


async def _poll_prediction(get_url: str) -> dict:
    headers = {"Authorization": f"Bearer {settings.replicate_api_token}"}
    delay = 1.0
    deadline = time.monotonic() + MAX_POLL_TIMEOUT

    async with httpx.AsyncClient(timeout=30.0) as client:
        while time.monotonic() < deadline:
            resp = await client.get(get_url, headers=headers)
            prediction = resp.json()
            status = prediction.get("status")
            if status in ("succeeded", "failed", "canceled", "aborted"):
                return prediction
            await asyncio.sleep(min(delay, 30.0))
            delay = min(delay * 2, 30.0)

    raise TTSError(f"Prediction timed out after {MAX_POLL_TIMEOUT}s")


async def _download_audio(url: str, output_path: Path) -> None:
    headers = {"Authorization": f"Bearer {settings.replicate_api_token}"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url, headers=headers, follow_redirects=True)
    if resp.status_code != 200:
        raise TTSError(f"Audio download failed: {resp.status_code}")
    await write_bytes(output_path, resp.content)


def _stitch_segments(segment_paths: list[Path], silence_path: Path, output_path: Path) -> None:
    """Use ffmpeg concat to stitch segments with silence between them."""
    concat_list_path = output_path.parent / "concat_list.txt"
    silence_abs = silence_path.resolve()

    lines = []
    for i, seg in enumerate(segment_paths):
        lines.append(f"file '{seg.resolve()}'")
        if i < len(segment_paths) - 1:
            lines.append(f"file '{silence_abs}'")

    concat_list_path.write_text("\n".join(lines))

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_path),
            "-ar", "24000",
            "-ac", "1",
            "-b:a", "96k",
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise TTSError(f"ffmpeg failed: {result.stderr[-500:]}")

    logger.info("Stitched audio: %s", output_path)
