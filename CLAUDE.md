# Comp Agent — Guidance for AI Assistants

This file is read by Claude Code and (via symlink or copy as `AGENTS.md`) by Codex. Read it before making changes.

## What this project is

Comp Agent is a **backend agent** that produces comparative-projects research decks (PPTX + JSON artifacts) for real estate design work. It is one of several backend agents in a larger system. A separate frontend, owned by another developer, will call into this agent over HTTP and consume its outputs.

This repo is **not** the frontend, the orchestrator, or a general-purpose service. Keep the scope narrow.

## Core constraints — do not violate without explicit approval

1. **No framework rewrites.** The `BaseHTTPRequestHandler` server in `src/comp_agent/ui.py` is intentional for local dev. Do not swap it for FastAPI/Flask/etc. unless the user explicitly asks.
2. **Stage boundaries are stable.** The methods on `CompAppStages` (`discover`, `approve`, `research`, `format_outputs`, `generate_outputs`, `audit`, `run_poc`) are the agent's public Python API. Their signatures and the JSON files they write are contracts — see `docs/stage_contracts.md`. Do not rename, restructure, or change return shapes.
3. **File-based state is intentional.** Each stage writes JSON/CSV checkpoints under `projects/<project_slug>/`. Downstream stages read from disk. Do not introduce a database, in-memory pipeline, or cross-stage object passing.
4. **Dataclasses in `models.py` are the wire format.** They serialize via `to_jsonable()` / `asdict()`. Adding fields is fine; renaming or removing fields is a breaking change for downstream consumers (the frontend, other agents, archived project folders).
5. **The test suite is the contract.** All 27 tests in `tests/test_pipeline.py` must stay green. If a change requires updating a test assertion about output structure, that's a signal you may be breaking the contract — stop and ask.

## Working style

- **Prefer small, surgical edits.** This codebase will be touched by multiple AI assistants across sessions; large refactors create merge pain and silent contract drift.
- **Don't add abstractions for hypothetical needs.** No plugin systems, no provider registries beyond what's already there, no config frameworks.
- **Don't add dependencies casually.** Current deps: `python-pptx`, `Pillow`, `openai` (implicit via Responses API). Adding anything else needs justification.
- **Don't write new docs unless asked.** README, `docs/`, and this file are sufficient.

## Things that are fair game

- Bug fixes within a stage's existing logic.
- Adding new fields to existing dataclasses (additive only).
- Tightening existing tests, adding new tests.
- Tuning prompts in `openai_search.py`.
- Improving image validation, source dedup, audit checks.
- Making hardcoded values configurable via env var (e.g., the model name in `openai_search.py`).

## Things that need user approval first

- Any change to `CompAppStages` method names, signatures, or return-dict keys.
- Any change to the JSON/CSV file names or schemas listed in `docs/stage_contracts.md`.
- Adding a new dependency.
- Replacing the HTTP server, adding auth, adding a database.
- Changing the PPTX layout, color scheme, or branding.

## Environment notes

- Python 3.10+, Windows + bash via Claude Code (use forward slashes).
- `OPENAI_API_KEY` lives in `.env` (see `.env.example`). Live search is optional — code falls back to placeholders when absent.
- Tests run with `pytest`; do not skip or `xfail` to make a change land.
