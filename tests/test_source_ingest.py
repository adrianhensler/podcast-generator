import pytest
from unittest.mock import AsyncMock, patch

from app.services.source_ingest import IngestionError, ingest


@pytest.mark.asyncio
async def test_successful_ingest_uses_httpx_content(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.output_dir", str(tmp_path))
    content = "This is a long enough article content " * 10  # > 200 chars

    with patch("app.services.source_ingest._httpx_extract", new=AsyncMock(return_value=content)) as mock_httpx, patch(
        "app.services.source_ingest._trafilatura_extract"
    ) as mock_trafilatura:
        result = await ingest("test-proj", "https://example.com")

    assert result == content
    assert (tmp_path / "test-proj" / "normalized_sources.md").exists()
    mock_httpx.assert_awaited_once_with("https://example.com")
    mock_trafilatura.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_falls_back_to_trafilatura_when_httpx_short(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.output_dir", str(tmp_path))
    httpx_content = "Too short"
    trafilatura_content = "This fallback content is long enough for ingestion " * 8  # > 200 chars

    with patch(
        "app.services.source_ingest._httpx_extract", new=AsyncMock(return_value=httpx_content)
    ) as mock_httpx, patch(
        "app.services.source_ingest._trafilatura_extract", return_value=trafilatura_content
    ) as mock_trafilatura:
        result = await ingest("test-proj", "https://example.com")

    assert result == trafilatura_content
    mock_httpx.assert_awaited_once_with("https://example.com")
    mock_trafilatura.assert_called_once_with("https://example.com")


@pytest.mark.asyncio
@pytest.mark.parametrize("httpx_content,trafilatura_content", [(None, None), ("short", "tiny")])
async def test_ingest_raises_when_both_extractors_insufficient(httpx_content, trafilatura_content):
    with patch(
        "app.services.source_ingest._httpx_extract", new=AsyncMock(return_value=httpx_content)
    ) as mock_httpx, patch(
        "app.services.source_ingest._trafilatura_extract", return_value=trafilatura_content
    ) as mock_trafilatura:
        with pytest.raises(IngestionError) as exc_info:
            await ingest("test-proj", "https://example.com")

    assert "minimum 200 required" in str(exc_info.value)
    mock_httpx.assert_awaited_once_with("https://example.com")
    mock_trafilatura.assert_called_once_with("https://example.com")


@pytest.mark.asyncio
async def test_tavily_augment_isolated_with_patch(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.output_dir", str(tmp_path))
    content = "Long enough content for the test " * 10

    with patch("app.services.source_ingest._httpx_extract", new=AsyncMock(return_value=content)), patch(
        "app.services.source_ingest._tavily_augment", new=AsyncMock(return_value=None)
    ) as mock_tavily:
        result = await ingest("test-proj2", "https://example.com", use_tavily=True)

    assert result == content
    mock_tavily.assert_awaited_once_with("https://example.com")
