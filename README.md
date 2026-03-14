# Research Podcast Studio

Turn any URL into a dual-host research podcast with editable brief and script.

![Research Podcast Studio](https://raw.githubusercontent.com/adrianhensler/podcast-generator/main/docs/screenshot.png)

### Example output

[Halifax, Nova Scotia — example podcast episode](docs/example.mp3)

## Quick Start

```bash
# Copy and fill in API keys
cp .env.example .env

# Run with Docker
docker compose up --build

# Or run locally
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://localhost:8000

## Workflow

1. **URL** → paste any article/page URL and choose options
2. **Research Brief** → streams token-by-token into the editor; revise in plain English
3. **Script** → outline (fast, sync) then expansion streams live into the editor; revise in plain English
4. **Audio** → TTS render with voice selection, download MP3

## UX

- All three stages are always visible; sections unlock progressively as work completes
- Brief and script generation stream token-by-token via SSE — no spinner-then-reload
- Revision prompts on each stage: describe changes in plain English, watch the rewrite stream in
- Page refreshes during streaming resume automatically (brief_streaming / script_streaming)
- Auto-scroll to the active section on page load

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes | For LLM calls (brief + script generation) |
| `REPLICATE_API_TOKEN` | Yes | For TTS audio rendering |
| `TAVILY_API_KEY` | No | Optional web search augmentation |
| `REPLICATE_CONCURRENCY` | No | Max concurrent TTS requests (default: 5) |

## Features

- URL → Research Brief → Script → Audio end-to-end
- SSE streaming for brief and script generation
- Plain-English revision prompts on brief and script
- 1 or 2 speakers, tone control, length control
- Voice selection from 17 curated minimax voices
- Project history sidebar with inline audio playback
- Cost and token tracking per stage
- Optional Tavily web search augmentation

## Models

- **Brief + Outline + Revisions**: `qwen/qwen3.5-397b-a17b` via OpenRouter
- **Script expand + Revisions**: `z-ai/glm-5` via OpenRouter
- **TTS**: `minimax/speech-02-turbo` via Replicate

## Status flow

```
pending → ingesting → brief_pending → brief_streaming → brief_ready
  → scripting → script_outline → script_streaming → script_ready
  → rendering → done
```

## Tests

```bash
pytest tests/ -v
```
