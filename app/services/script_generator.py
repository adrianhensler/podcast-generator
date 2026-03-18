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


def _lang_instruction(language: str) -> str:
    if language and language.lower() not in ("english", "auto", ""):
        return f"Write all output in {language}.\n"
    return ""

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

# Per-flow-type prompt configuration.
# Each entry controls: outline framing, host personas, and speaker interaction rules.
FLOW_CONFIGS: dict[str, dict] = {
    "explainer": {
        "label": "Explainer",
        "outline_framing": "A clear, accessible explanation for a general audience. Build understanding step by step.",
        "persona_a": "Host A is curious and accessible, asking the questions a thoughtful listener would ask.",
        "persona_b": "Host B is knowledgeable and clear, explaining concepts without jargon.",
        "speaker_instruction": (
            "Host A introduces topics and asks questions. Host B provides explanation and context. "
            "Natural back-and-forth — not monologues."
        ),
    },
    "review": {
        "label": "Review / Analysis",
        "outline_framing": (
            "A structured review and evaluation. The hook should frame the core question being evaluated. "
            "key_points should cover strengths, weaknesses, and signals pointing toward a verdict."
        ),
        "persona_a": "Host A is a skeptic who probes for weaknesses and asks hard questions.",
        "persona_b": "Host B is an analyst who weighs evidence and builds toward a clear verdict.",
        "speaker_instruction": (
            "Hosts critically evaluate the subject together. Work toward a clear verdict or recommendation. "
            "The outro must deliver a concrete conclusion — not 'it depends'."
        ),
    },
    "debate": {
        "label": "Debate",
        "outline_framing": (
            "A structured debate. Host A argues FOR the central proposition. Host B argues AGAINST it. "
            "key_points should list the strongest arguments from each side."
        ),
        "persona_a": "Host A is an advocate FOR the central proposition — argues it confidently and marshals supporting evidence.",
        "persona_b": "Host B argues AGAINST the central proposition — challenges assumptions and raises counterpoints.",
        "speaker_instruction": (
            "Hosts actively disagree and challenge each other's claims. "
            "Each host should push back directly when the other makes a point. "
            "End with each host briefly restating their position."
        ),
    },
    "interview": {
        "label": "Interview",
        "outline_framing": (
            "An interview format. questions should be the prepared interview questions Host A will ask. "
            "key_points are the core insights Host B (the expert) will convey through their answers."
        ),
        "persona_a": "Host A is the interviewer — asks prepared, probing questions and follows up naturally on interesting answers.",
        "persona_b": "Host B is the subject-matter expert — answers from deep knowledge, adds context the interviewer didn't ask for.",
        "speaker_instruction": (
            "Host A asks questions only — no lectures or long explanations from the interviewer. "
            "Host B provides answers and elaborates freely. Host A may follow up but stays in the questioning role."
        ),
    },
    "deep_dive": {
        "label": "Deep Dive",
        "outline_framing": (
            "A thorough, evidence-forward deep dive for a knowledgeable audience. "
            "key_points should be specific, evidence-based claims. risks_caveats should be detailed and precise."
        ),
        "persona_a": "Host A is a researcher who has read everything and asks precise, detailed questions about mechanisms and evidence.",
        "persona_b": "Host B is a domain expert who goes deep on underlying mechanisms, data quality, and nuanced implications.",
        "speaker_instruction": (
            "Both hosts go deep. Cite specific facts and figures from the brief. "
            "Use longer turns — this is serious analysis, not banter. "
            "Prioritize accuracy and depth over entertainment value."
        ),
    },
}

DEFAULT_FLOW = "explainer"


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
    flow_type: str = DEFAULT_FLOW,
) -> tuple[str, list[StageLogData]]:
    """Two-stage script generation. Returns (script_text, [stage_logs])."""
    target_words = LENGTH_TARGETS.get(length, 1500)
    logs = []

    # Stage A: Outline
    outline_json, log_a = await _generate_outline(brief, num_speakers, tone, target_words, flow_type=flow_type)
    logs.append(log_a)

    # Stage B: Expand
    script, log_b = await _expand_to_script(brief, outline_json, num_speakers, tone, length, flow_type=flow_type)
    logs.append(log_b)

    file_path = await write_artifact(project_id, "script.md", script)
    logger.info("Generated script: %d chars → %s", len(script), file_path)
    return script, logs


async def _generate_outline(
    brief: str, num_speakers: int, tone: str, target_words: int, language: str = "English",
    flow_type: str = DEFAULT_FLOW,
) -> tuple[str, StageLogData]:
    speaker_note = "one host" if num_speakers == 1 else "two hosts (Host A and Host B)"
    tone_note = {
        "positive": "optimistic and forward-looking",
        "negative": "critical and cautionary",
        "neutral": "balanced and objective",
    }.get(tone, "balanced")

    flow_cfg = FLOW_CONFIGS.get(flow_type, FLOW_CONFIGS[DEFAULT_FLOW])
    lang_note = f"Write all segment text in {language}. " if language.lower() not in ("english", "auto", "") else ""

    prompt = f"""{_lang_instruction(language)}Create a podcast episode outline for {speaker_note} in a {tone_note} style.
Episode format: {flow_cfg["outline_framing"]}
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
}}
{lang_note}"""

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
    brief: str, outline_dict: dict, num_speakers: int, tone: str, length: str, language: str = "English",
    flow_type: str = DEFAULT_FLOW,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for script expansion."""
    target_words = LENGTH_TARGETS.get(length, 1500)

    flow_cfg = FLOW_CONFIGS.get(flow_type, FLOW_CONFIGS[DEFAULT_FLOW])

    if num_speakers == 1:
        speaker_instruction = """Format: Every line starts with "Host A:" followed by the dialogue.
No Host B lines. Host A speaks continuously, using natural transitions."""
        persona_desc = "Host A is knowledgeable, engaging, and speaks directly to the listener."
    else:
        speaker_instruction = (
            f'Format: Alternate between "Host A:" and "Host B:" lines.\n'
            f"{flow_cfg['speaker_instruction']}\n"
            "Each speaker turn must start on its own line with exactly \"Host A:\" or \"Host B:\"."
        )
        persona_desc = f"{flow_cfg['persona_a']} {flow_cfg['persona_b']}"

    prompt = f"""{_lang_instruction(language)}Expand this outline into a full podcast script of approximately {target_words} words.

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
    brief: str, outline_json: str, num_speakers: int, tone: str, length: str, language: str = "English",
    flow_type: str = DEFAULT_FLOW,
) -> tuple[str, StageLogData]:
    try:
        outline = json.loads(outline_json)
    except (json.JSONDecodeError, TypeError):
        # Try to extract JSON from the response if wrapped in markdown or empty
        match = re.search(r'\{.*\}', outline_json or '', re.DOTALL)
        outline = json.loads(match.group()) if match else {}

    system_prompt, prompt = build_expand_prompt(brief, outline, num_speakers, tone, length, language, flow_type=flow_type)

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


OUTRO_SYSTEM = """You are a podcast scriptwriter writing a closing segment for a podcast episode.
Rules:
- Write in natural spoken language only.
- Spell out ALL numbers and figures.
- No markdown: no asterisks, headers, bullet points, hyphens as bullets.
- No symbols: no $, %, #, &, @, →, —.
- No URLs or domain names.
- Keep lines to 1-3 sentences per turn.
- Each speaker turn must start with exactly "Host A:" or "Host B:" on its own line.
- Do NOT use filler sign-offs like "that's all for today", "thanks for listening", or "see you next time"."""

# Flow-specific outro instructions
_FLOW_OUTRO_INSTRUCTION: dict[str, str] = {
    "explainer": "Summarise the single clearest takeaway, then give the listener one concrete thing to do or think about.",
    "review": "Deliver a clear verdict — do not hedge or say 'it depends'. State what the listener should do with this information.",
    "debate": "Each host briefly restates their position in one sentence. Do not force consensus — let the disagreement stand.",
    "interview": "The interviewer thanks the expert and lands the single most memorable insight from the conversation.",
    "deep_dive": "Pull out the most significant implication of the evidence. Give the listener a precise next step grounded in the facts discussed.",
}

# Number of trailing script turns to treat as draft outro context (replaced by generated outro)
_OUTRO_CONTEXT_TURNS = 5


def _split_script_body_and_draft_outro(script: str, n_turns: int = _OUTRO_CONTEXT_TURNS) -> tuple[str, str]:
    """Split script into (body, draft_outro).

    Body is everything except the last n_turns speaker turns.
    Draft outro is the last n_turns turns — used as context for generation, not kept verbatim.
    Returns (body_text, draft_outro_text).
    """
    pattern = re.compile(r'(?m)^(Host [AB]:.*(?:\n(?!Host [AB]:).+)*)', re.MULTILINE)
    turns = list(pattern.finditer(script))

    if len(turns) <= n_turns:
        # Script is very short — use the whole thing as context, body is empty
        return "", script

    split_pos = turns[-n_turns].start()
    return script[:split_pos].rstrip(), script[split_pos:]


def build_outro_prompt(
    hook: str,
    key_points: list[str],
    next_steps: list[str],
    draft_outro: str,
    num_speakers: int,
    flow_type: str = DEFAULT_FLOW,
    language: str = "English",
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for outro generation."""
    flow_cfg = FLOW_CONFIGS.get(flow_type, FLOW_CONFIGS[DEFAULT_FLOW])
    outro_instruction = _FLOW_OUTRO_INSTRUCTION.get(flow_type, _FLOW_OUTRO_INSTRUCTION["explainer"])

    if num_speakers == 1:
        format_note = 'Format: every line starts with "Host A:" — no Host B lines.'
        persona_note = "Host A is wrapping up the episode."
    else:
        format_note = 'Format: alternate "Host A:" and "Host B:" lines, 3-6 turns total.'
        persona_note = f"{flow_cfg['persona_a']} {flow_cfg['persona_b']}"

    kp_text = "\n".join(f"- {p}" for p in key_points) if key_points else "(none)"
    ns_text = "\n".join(f"- {s}" for s in next_steps) if next_steps else "(none)"
    lang_prefix = _lang_instruction(language)

    user_prompt = f"""{lang_prefix}Write a closing outro segment for this podcast episode (~150 words, {format_note}).

Opening hook the episode started with:
{hook}

Key points covered:
{kp_text}

Planned next steps / listener actions:
{ns_text}

How the episode was ending (draft — rewrite this into a proper outro, do not copy it verbatim):
{draft_outro}

Outro instruction for this episode format: {outro_instruction}

{persona_note}

Callback to the opening hook. Land the sharpest conclusion. Deliver a concrete takeaway. End cleanly."""

    return OUTRO_SYSTEM, user_prompt


async def generate_outro(
    outline_dict: dict,
    draft_outro: str,
    num_speakers: int,
    flow_type: str = DEFAULT_FLOW,
    language: str = "English",
) -> tuple[str, StageLogData]:
    """Generate a replacement outro from the draft ending and outline context."""
    hook = outline_dict.get("hook", "")
    key_points = outline_dict.get("key_points", [])
    next_steps = outline_dict.get("next_steps", [])

    system_prompt, user_prompt = build_outro_prompt(
        hook, key_points, next_steps, draft_outro, num_speakers, flow_type, language
    )

    outro, log = await llm_complete(
        model=settings.model_outline,  # fast/cheap model is fine for a short focused pass
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        max_tokens=600,
        stage_label="outro",
    )
    return outro.strip(), log


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
