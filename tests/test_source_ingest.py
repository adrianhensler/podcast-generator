import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.source_ingest import ingest, IngestionError


@pytest.mark.asyncio
async def test_successful_ingest(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.output_dir", str(tmp_path))

    content = "This is a long enough article content " * 10  # > 200 chars

    with patch("app.services.source_ingest._trafilatura_extract", return_value=content):
        result = await ingest("test-proj", "https://example.com")

    assert result == content
    assert (tmp_path / "test-proj" / "normalized_sources.md").exists()


@pytest.mark.asyncio
async def test_none_content_raises(monkeypatch):
    with patch("app.services.source_ingest._trafilatura_extract", return_value=None):
        with pytest.raises(IngestionError) as exc_info:
            await ingest("test-proj", "https://example.com")
    assert "Could not extract" in str(exc_info.value)


@pytest.mark.asyncio
async def test_short_content_raises(monkeypatch):
    with patch("app.services.source_ingest._trafilatura_extract", return_value="Short"):
        with pytest.raises(IngestionError) as exc_info:
            await ingest("test-proj", "https://example.com")
    assert "minimum 200 required" in str(exc_info.value)


@pytest.mark.asyncio
async def test_tavily_skipped_when_no_key(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.output_dir", str(tmp_path))
    monkeypatch.setattr("app.config.settings.tavily_api_key", "")

    content = "Long enough content for the test " * 10

    with patch("app.services.source_ingest._trafilatura_extract", return_value=content):
        result = await ingest("test-proj2", "https://example.com", use_tavily=True)

    # Should succeed without Tavily augmentation
    assert "Tavily" not in result
