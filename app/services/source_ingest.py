import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.services.storage import write_artifact

logger = logging.getLogger(__name__)


class IngestionError(Exception):
    pass


async def ingest(project_id: str, url: str, use_tavily: bool = False) -> tuple[str, list]:
    """
    Fetch and extract text from URL. Optionally augment with Tavily search.
    Returns (normalized_source_content, stage_logs) and writes normalized_sources.md.
    Tavily results are written to a separate tavily_results.md artifact.
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

    stage_logs = []
    if use_tavily:
        query, query_log = await _generate_tavily_query(content[:500], url)
        if query_log:
            stage_logs.append(query_log)
        tavily_content = await _tavily_augment(url, query)
        if tavily_content:
            await write_artifact(project_id, "tavily_results.md", tavily_content)
            logger.info("Wrote tavily_results.md for project %s", project_id)

    file_path = await write_artifact(project_id, "normalized_sources.md", content)
    logger.info("Ingested %d chars from %s → %s", len(content), url, file_path)
    return content, stage_logs


async def ingest_tavily_only(project_id: str, url: str, normalized_sources_preview: str) -> tuple[str, list]:
    """Re-run Tavily search for an existing project. Overwrites (or clears) tavily_results.md.
    Returns (tavily_content, stage_logs) — content is empty string if nothing found."""
    query, query_log = await _generate_tavily_query(normalized_sources_preview[:500], url)
    stage_logs = [query_log] if query_log else []
    tavily_content = await _tavily_augment(url, query)
    stale_path = Path(settings.output_dir) / project_id / "tavily_results.md"
    if tavily_content:
        await write_artifact(project_id, "tavily_results.md", tavily_content)
        logger.info("Re-ran Tavily for project %s → %d chars", project_id, len(tavily_content))
    else:
        # Clear stale artifact so downstream doesn't use outdated data
        if stale_path.exists():
            stale_path.unlink()
            logger.info("Cleared stale tavily_results.md for project %s (re-run returned no content)", project_id)
    return tavily_content or "", stage_logs


async def _generate_tavily_query(content_preview: str, url: str) -> tuple[str, object]:
    """Generate a focused 2-3 keyword search query using the LLM. Falls back to URL slug.
    Returns (query, StageLogData_or_None)."""
    try:
        from app.services.llm_client import llm_complete
        content, log = await llm_complete(
            model=settings.model_outline,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Article excerpt:\n{content_preview}\n\n"
                        "Generate a 2-3 keyword search query capturing what this article is about. "
                        "Return only the query, nothing else."
                    ),
                }
            ],
            temperature=0.3,
            max_tokens=32,
            stage_label="tavily_query",
        )
        query = content.strip().strip('"').strip("'")
        if query:
            logger.info("LLM-generated Tavily query: %r", query)
            return query, log
    except Exception as e:
        logger.warning("Tavily query generation failed, using URL slug fallback: %s", e)

    # Fallback: slug from URL path (no LLM cost to track)
    parsed = urlparse(url)
    slug = parsed.path.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " ").strip()
    return slug or parsed.netloc, None


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


def _pdf_bytes_extract(data: bytes) -> str | None:
    try:
        import pypdf, io
        reader = pypdf.PdfReader(io.BytesIO(data))
        pages = [p.extract_text() or "" for p in reader.pages]
        return "\n\n".join(p for p in pages if p.strip()) or None
    except Exception as e:
        logger.warning("PDF extraction failed: %s", e)
        return None


def extract_upload(filename: str, data: bytes) -> str:
    """Extract text from an uploaded file (PDF, TXT, MD). Raises IngestionError if too short."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "pdf":
        content = _pdf_bytes_extract(data)
    elif ext in ("txt", "md"):
        content = data.decode("utf-8", errors="replace")
    else:
        raise IngestionError(f"Unsupported file type: .{ext}. Use PDF, TXT, or MD.")
    if not content or len(content) < 200:
        raise IngestionError(
            f"Could not extract enough text from {filename} "
            f"({len(content or '')} chars; minimum 200). "
            "If this is a scanned PDF, it may not contain selectable text."
        )
    return content


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

        # Check if response is a PDF
        content_type = resp.headers.get("content-type", "")
        if "application/pdf" in content_type or url.lower().split("?")[0].endswith(".pdf"):
            return _pdf_bytes_extract(resp.content)

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


async def _tavily_augment(url: str, query: str) -> str | None:
    api_key = settings.tavily_api_key
    if not api_key:
        logger.warning("use_tavily=True but TAVILY_API_KEY not configured; skipping")
        return None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": "advanced",
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
