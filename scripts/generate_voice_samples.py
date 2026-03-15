"""
Generate a short sample MP3 for every available voice.

Usage (from repo root):
    python scripts/generate_voice_samples.py

Output: output/voice_samples/<voice_id>.mp3
"""

import asyncio
import sys
from pathlib import Path

# Allow running from repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # loads .env
from app.services.tts_renderer import _render_segment, REPLICATE_API_URL_TURBO

VOICES = [
    "Wise_Woman",
    "Friendly_Person",
    "Inspirational_girl",
    "Deep_Voice_Man",
    "Calm_Woman",
    "Casual_Guy",
    "Lively_Girl",
    "Patient_Man",
    "Young_Knight",
    "Determined_Man",
    "Lovely_Girl",
    "Decent_Boy",
    "Imposing_Manner",
    "Elegant_Man",
    "Abbess",
    "English_WhimsicalGirl",
    "English_Jovialman",
]

VOICE_LABELS = {
    "Young_Knight": "Young Woman",
    "English_WhimsicalGirl": "Whimsical Girl",
    "English_Jovialman": "Jovial Man",
}

OUTPUT_DIR = Path(settings.output_dir) / "voice_samples"


async def generate_sample(voice_id: str, semaphore: asyncio.Semaphore) -> tuple[str, bool, str]:
    display_name = VOICE_LABELS.get(voice_id, voice_id.replace("_", " "))
    text = f"This is a sample of voice {display_name}."
    out_path = OUTPUT_DIR / f"{voice_id}.mp3"
    async with semaphore:
        try:
            await _render_segment(text, voice_id, out_path, REPLICATE_API_URL_TURBO)
            size_kb = out_path.stat().st_size // 1024
            return voice_id, True, f"{size_kb} KB"
        except Exception as exc:
            return voice_id, False, str(exc)


async def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(settings.replicate_concurrency)

    print(f"Generating samples for {len(VOICES)} voices → {OUTPUT_DIR}\n")
    tasks = [generate_sample(v, semaphore) for v in VOICES]
    results = await asyncio.gather(*tasks)

    ok = sum(1 for _, success, _ in results if success)
    print(f"{'Voice':<22}  {'Status':<8}  Detail")
    print("-" * 50)
    for voice_id, success, detail in sorted(results):
        status = "OK" if success else "FAILED"
        print(f"{voice_id:<22}  {status:<8}  {detail}")

    print(f"\n{ok}/{len(VOICES)} voices generated successfully.")
    if ok < len(VOICES):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
