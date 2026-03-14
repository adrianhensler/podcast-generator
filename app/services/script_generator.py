import json
import logging
import re
from dataclasses import dataclass

from app.config import settings
from app.services.llm_client import llm_complete, StageLogData
from app.services.storage import write_artifact

logger = logging.getLogger(__name__)

LENGTH_TARGETS = {"short": 800, "medium": 1500, "long": 2500}

OUTLINE_SYSTEM = """You are a podcast producer creating episode outlines.
Output valid JSON only. No markdown, no preamble."""

EXPAND_SYSTEM = """You are a podcast scriptwriter producing natural, spoken dialogue.
Rules:
- Write in natural spoken language only.
- Spell out ALL numbers and figures ("one hundred dollars", "three point five percent").
- No markdown: no asterisks, headers, bullet points, hyphens as bullets.
- No symbols: no $, %, #, &, @, →, —.
- No URLs or domain names.
- No numbered or bulleted lists — weave all points into flowing speech.
- Use commas and periods for natural pauses only.
- Keep lines to natural speech segments (1-3 sentences per turn).
- Each speaker turn must start with exactly "Host A:" or "Host B:" on its own line."""


class ScriptParseError(Exception):
    pass


@dataclass
class ScriptLine:
    speaker: str  # "Host A" or "Host B"
    text: str


async def generate(
    project_id: str,
    brief: str,
    num_speakers: int = 2,
    tone: str = "neutral",
    length: str = "medium",
) -> tuple[str, list[StageLogData]]:
    """Two-stage script generation. Returns (script_text, [stage_logs])."""
    target_words = LENGTH_TARGETS.get(length, 1500)
    logs = []

    # Stage A: Outline
    outline_json, log_a = await _generate_outline(brief, num_speakers, tone, target_words)
    logs.append(log_a)

    # Stage B: Expand
    script, log_b = await _expand_to_script(brief, outline_json, num_speakers, tone, length)
    logs.append(log_b)

    file_path = await write_artifact(project_id, "script.md", script)
    logger.info("Generated script: %d chars → %s", len(script), file_path)
    return script, logs


async def _generate_outline(
    brief: str, num_speakers: int, tone: str, target_words: int
) -> tuple[str, StageLogData]:
    speaker_note = "one host" if num_speakers == 1 else "two hosts (Host A and Host B)"
    tone_note = {
        "positive": "optimistic and forward-looking",
        "negative": "critical and cautionary",
        "neutral": "balanced and objective",
    }.get(tone, "balanced")

    prompt = f"""Create a podcast episode outline for {speaker_note} in a {tone_note} style.
Target script length: ~{target_words} words.

Research Brief:
{brief}

Return a JSON object with this structure:
{{
  "hook": "opening hook sentence",
  "questions": ["question 1", "question 2", "question 3"],
  "key_points": ["point 1", "point 2", "point 3", "point 4"],
  "risks_caveats": ["caveat 1", "caveat 2"],
  "next_steps": ["action 1", "action 2"]
}}"""

    content, log = await llm_complete(
        model=settings.model_outline,
        messages=[
            {"role": "system", "content": OUTLINE_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
        max_tokens=1024,
        stage_label="outline",
        response_format={"type": "json_object"},
    )
    return content, log


def build_expand_prompt(
    brief: str, outline_dict: dict, num_speakers: int, tone: str, length: str
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for script expansion."""
    target_words = LENGTH_TARGETS.get(length, 1500)

    if num_speakers == 1:
        speaker_instruction = """Format: Every line starts with "Host A:" followed by the dialogue.
No Host B lines. Host A speaks continuously, using natural transitions."""
        persona_desc = "Host A is knowledgeable, engaging, and speaks directly to the listener."
    else:
        speaker_instruction = """Format: Alternate between "Host A:" and "Host B:" lines.
Host A introduces topics and asks questions. Host B provides analysis and commentary.
Natural back-and-forth — not monologues. Each speaker contributes meaningfully."""
        persona_desc = "Host A is curious and accessible. Host B is analytical and direct."

    prompt = f"""Expand this outline into a full podcast script of approximately {target_words} words.

{persona_desc}

Outline:
{json.dumps(outline_dict, indent=2)}

Research Brief (source of all facts):
{brief}

{speaker_instruction}

Start with a compelling hook. End with a clear "what to do next" or takeaway.
Every factual claim must come from the research brief above."""

    return EXPAND_SYSTEM, prompt


async def _expand_to_script(
    brief: str, outline_json: str, num_speakers: int, tone: str, length: str
) -> tuple[str, StageLogData]:
    try:
        outline = json.loads(outline_json)
    except (json.JSONDecodeError, TypeError):
        # Try to extract JSON from the response if wrapped in markdown or empty
        match = re.search(r'\{.*\}', outline_json or '', re.DOTALL)
        outline = json.loads(match.group()) if match else {}

    system_prompt, prompt = build_expand_prompt(brief, outline, num_speakers, tone, length)

    script, log = await llm_complete(
        model=settings.model_expand,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=0.8,
        max_tokens=6000,
        stage_label="expand",
    )
    return script, log


def parse_script_lines(script: str) -> list[ScriptLine]:
    """Parse Host A:/Host B: lines from script text."""
    pattern = re.compile(r'^\s*(Host [AB]):\s*(.+?)\s*$', re.MULTILINE)
    matches = pattern.findall(script)

    if not matches:
        preview = script[:200].replace('\n', '\\n')
        raise ScriptParseError(
            f"No Host A:/Host B: lines found in script. Preview: {preview!r}"
        )

    lines = []
    for speaker, text in matches:
        if len(text) > 9800:
            logger.warning("Script line from %s truncated: %d chars", speaker, len(text))
            text = text[:9800]
        lines.append(ScriptLine(speaker=speaker, text=text))

    return lines
