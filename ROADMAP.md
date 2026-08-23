# Roadmap — Research Podcast Studio

Features planned for v1.5+.

> **Note:** This roadmap isn't kept in sync with the parent `test.hensler.work` repo's own tracked podcast roadmap in `docs/dev-log.md` (the "6-chunk podcast improvement roadmap"), which is where actual progress gets checked off. Items below marked done were caught stale here as of 2026-08-23 — check `docs/dev-log.md` in the parent repo for what's current if this file hasn't been touched in a while.

## Sources
- Document upload (PDF, txt, md, docx)
- Multi-URL ingestion
- gpt-researcher deep research integration
- Source weighting (primary vs supporting)
- Duplicate/near-duplicate detection

## Research
- Depth mode: quick / standard / deep
- Audience level: beginner / general / technical / executive
- Citation strictness toggle
- Explicit uncertainty policy

## Script
- ✅ Format presets — shipped as "flow types": explainer, review, debate, interview, deep_dive (`app/models.py`, `app/services/script_generator.py`'s `FLOW_CONFIGS`)
- Host relationship: collaborative vs adversarial
- Banter/interruptions slider
- Optional fact-check pass

## Audio
- Speech normalization preprocessing layer
- Intro/outro stingers
- Loudness normalization
- Language + accent selection per voice
- Chapter/time markers

## Infrastructure
- Authentication / user accounts — satisfied at the product level by the parent repo's Caddy `forward_auth` gate + SSO, not by anything inside this submodule
- Cloud storage (S3/R2)
- ✅ Background job queue — Celery, running as `podcast-backend`/`podcast-worker` in the parent repo
- ✅ Webhook delivery — `podcast-backend/app/api/routes/webhooks.py` in the parent repo
- Transcript/SRT export
- ✅ Public result pages — `/podcast/share/*`, no-auth route in the parent repo's Caddyfile
- Multilingual support

## Cost & Safety
- Pre-run cost estimate
- Hard budget cap per run
- Per-stage cost breakdown UI
- One-click rerun from any stage
