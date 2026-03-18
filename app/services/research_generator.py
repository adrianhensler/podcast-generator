import logging

from app.config import settings
from app.services.llm_client import llm_complete, StageLogData
from app.services.storage import write_artifact

logger = logging.getLogger(__name__)

LENGTH_WORDS = {"short": 300, "medium": 600, "long": 1000}

SYSTEM_PROMPT = """You are a research analyst producing factual briefings for podcast hosts.
Rules:
- Use ONLY facts from the provided source content.
- Never invent statistics, quotes, or events not present in the source.
- When uncertain, use uncertainty markers ("reportedly", "according to the source", "it appears").
- Write in clear, accessible prose — no bullet points, no headers.
- Focus on what matters most for an informed listener."""


def _lang_instruction(language: str) -> str:
    if language and language.lower() not in ("english", "auto", ""):
        return f"Write all output in {language}.\n"
    return ""


_FLOW_BRIEF_FRAMING: dict[str, str] = {
    "explainer": "",
    "review": (
        "Identify and emphasize strengths, weaknesses, and any signals that point toward a verdict or recommendation. "
        "Note what is impressive and what falls short."
    ),
    "debate": (
        "Present evidence and arguments supporting BOTH sides of the central proposition equally. "
        "Do not take a position — surface the strongest case for and against."
    ),
    "interview": (
        "Focus on the key claims, insights, and expertise the subject-matter expert would speak to. "
        "Highlight the most interesting and debatable points an interviewer could probe."
    ),
    "deep_dive": (
        "Extract all specific figures, statistics, citations, mechanisms, and technical details. "
        "Prioritize precision and completeness over brevity."
    ),
}


def build_brief_prompt(url: str, source_content: str, tone: str, length: str, language: str = "English",
                       flow_type: str = "explainer") -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for brief generation."""
    target_words = LENGTH_WORDS.get(length, 600)
    tone_instruction = {
        "positive": "Emphasize opportunities, strengths, and positive outcomes.",
        "negative": "Emphasize risks, weaknesses, and concerning trends.",
        "neutral": "Present all sides objectively without editorial slant.",
    }.get(tone, "Present all sides objectively.")

    flow_framing = _FLOW_BRIEF_FRAMING.get(flow_type, "")
    flow_note = f"\nAdditional focus for this episode format: {flow_framing}" if flow_framing else ""
    lang_prefix = _lang_instruction(language)

    user_prompt = f"""{lang_prefix}Source URL: {url}

Source Content:
{source_content}

Write a research brief of approximately {target_words} words. {tone_instruction}{flow_note}

Cover:
1. What happened / what this is about
2. Key facts, figures, and claims from the source
3. Context and background (from source only)
4. Significance and implications
5. Any caveats, uncertainties, or limitations noted in the source

Write as flowing paragraphs. Do not use bullet points or headers."""

    return SYSTEM_PROMPT, user_prompt


async def generate(
    project_id: str,
    url: str,
    source_content: str,
    tone: str = "neutral",
    length: str = "medium",
    language: str = "English",
    flow_type: str = "explainer",
) -> tuple[str, StageLogData]:
    """Generate a research brief from source content. Returns (brief_text, stage_log)."""
    system_prompt, user_prompt = build_brief_prompt(url, source_content, tone, length, language, flow_type=flow_type)

    brief, log = await llm_complete(
        model=settings.model_outline,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=2048,
        stage_label="brief",
    )

    file_path = await write_artifact(project_id, "research_brief.md", brief)
    logger.info("Generated brief: %d chars → %s", len(brief), file_path)
    return brief, log
