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


def build_brief_prompt(url: str, source_content: str, tone: str, length: str) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for brief generation."""
    target_words = LENGTH_WORDS.get(length, 600)
    tone_instruction = {
        "positive": "Emphasize opportunities, strengths, and positive outcomes.",
        "negative": "Emphasize risks, weaknesses, and concerning trends.",
        "neutral": "Present all sides objectively without editorial slant.",
    }.get(tone, "Present all sides objectively.")

    user_prompt = f"""Source URL: {url}

Source Content:
{source_content}

Write a research brief of approximately {target_words} words. {tone_instruction}

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
) -> tuple[str, StageLogData]:
    """Generate a research brief from source content. Returns (brief_text, stage_log)."""
    system_prompt, user_prompt = build_brief_prompt(url, source_content, tone, length)

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
