import asyncio
import logging

import httpx

from app.config import settings
from app.services.storage import write_artifact

logger = logging.getLogger(__name__)


class IngestionError(Exception):
    pass


async def ingest(project_id: str, url: str, use_tavily: bool = False) -> str:
    """
    Fetch and extract text from URL. Optionally augment with Tavily search.
    Returns the normalized source content and writes normalized_sources.md.
    """
    # Try httpx with browser headers first (works on sites that block headless fetchers)
    content = await _httpx_extract(url)

    # Fallback: trafilatura's own fetcher
    if not content or len(content) < 200:
        logger.info("httpx got %d chars, trying trafilatura native fetch", len(content or ""))
        content = await asyncio.to_thread(_trafilatura_extract, url)

    if not content or len(content) < 200:
        raise IngestionError(
            f"Could not extract enough content from {url}. "
            "The page may block scrapers or have little readable text. "
            f"Extracted: {len(content or '')} chars (minimum 200 required)."
        )

    if use_tavily:
        tavily_content = await _tavily_augment(url)
        if tavily_content:
            content = content + "\n\n---\n\n## Additional Context (Tavily)\n\n" + tavily_content

    file_path = await write_artifact(project_id, "normalized_sources.md", content)
    logger.info("Ingested %d chars from %s → %s", len(content), url, file_path)
    return content


BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    # Note: do NOT set Accept-Encoding — let httpx handle decompression automatically
}


def _trafilatura_extract(url: str) -> str | None:
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None
        return trafilatura.extract(downloaded, include_comments=False, include_tables=True)
    except Exception as e:
        logger.warning("trafilatura failed for %s: %s", url, e)
        return None


async def _httpx_extract(url: str) -> str | None:
    """Fetch with browser-like headers, then extract with trafilatura."""
    try:
        import trafilatura
        async with httpx.AsyncClient(
            timeout=30.0,
            headers=BROWSER_HEADERS,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)

        if resp.status_code != 200:
            logger.warning("httpx fallback got %s for %s", resp.status_code, url)
            return None

        # Pass raw bytes + url hint so trafilatura handles encoding correctly
        content = trafilatura.extract(
            resp.content,
            url=url,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        if content:
            logger.info("httpx fallback extracted %d chars from %s", len(content), url)
        return content
    except Exception as e:
        logger.warning("httpx fallback failed for %s: %s", url, e)
        return None


async def _tavily_augment(url: str) -> str | None:
    api_key = settings.tavily_api_key
    if not api_key:
        logger.warning("use_tavily=True but TAVILY_API_KEY not configured; skipping")
        return None

    try:
        # Extract a search query from the URL domain/path
        from urllib.parse import urlparse
        parsed = urlparse(url)
        query = f"site:{parsed.netloc} {parsed.path.replace('/', ' ').strip()}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": "basic",
                    "include_answer": True,
                    "max_results": 5,
                },
            )
        if resp.status_code != 200:
            logger.warning("Tavily returned %s: %s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()
        parts = []
        if data.get("answer"):
            parts.append(f"**Summary:** {data['answer']}")
        for r in data.get("results", []):
            parts.append(f"**{r.get('title', '')}**\n{r.get('content', '')}")
        return "\n\n".join(parts) if parts else None

    except Exception as e:
        logger.warning("Tavily augmentation failed: %s", e)
        return None
