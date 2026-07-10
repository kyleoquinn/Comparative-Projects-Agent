# Comp Agent — Guidance for AI Assistants

This file is read by Claude Code and (via symlink or copy as `AGENTS.md`) by Codex. Read it before making changes.

## What this project is

Comp Agent is a **standalone product** that produces comparative-projects research decks (PPTX + JSON artifacts) for real estate design work. The current phase is an **internal desktop distribution**: a PyInstaller onedir build launched from one shortcut on the office share (`X:\28_AI\_AI AGENTS`), which resolves the shared OpenAI key config over the network (UNC path, via `src/comp_agent/config.py`), starts a local server on `127.0.0.1`, and opens the built-in browser UI. A **hosted web service is a later phase** — the stage API, file artifacts, and local HTTP endpoints are the seam it will reuse, so keep them stable.

This repo is **not** a hosted service, an orchestrator, or a general-purpose framework. Keep the scope narrow. See `docs/standalone_migration_plan.md` for the migration context.

## Core constraints — do not violate without explicit approval

1. **No framework rewrites.** The `BaseHTTPRequestHandler` server in `src/comp_agent/ui.py` is intentional — it is the product's local UI server, packaged as-is into the desktop app. Do not swap it for FastAPI/Flask/etc. unless the user explicitly asks.
2. **Stage boundaries are stable.** The methods on `CompAppStages` (`discover`, `approve`, `research`, `format_outputs`, `generate_outputs`, `audit`, `run_poc`) are the agent's public Python API. Their signatures and the JSON files they write are contracts — see `docs/stage_contracts.md`. Do not rename, restructure, or change return shapes.
3. **File-based state is intentional.** Each stage writes JSON/CSV checkpoints under `projects/<project_slug>/`. Downstream stages read from disk. Do not introduce a database, in-memory pipeline, or cross-stage object passing.
4. **Dataclasses in `models.py` are the wire format.** They serialize via `to_jsonable()` / `asdict()`. Adding fields is fine; renaming or removing fields is a breaking change for downstream consumers (the built-in UI, a future hosted service, archived project folders).
5. **The test suite is the contract.** The full test suite in `tests/` (currently 89+ tests; run `pytest` for the live count) must stay green. If a change requires updating a test assertion about output structure, that's a signal you may be breaking the contract — stop and ask.
6. **Code identifiers stay `comp_agent`.** The package name, console scripts, env-var prefixes, and the `comp-agent` distribution name are load-bearing across code, tests, and the shared-drive config keys. A future product/display name goes in UI copy and docs only.

## Working style

- **Prefer small, surgical edits.** This codebase will be touched by multiple AI assistants across sessions; large refactors create merge pain and silent contract drift.
- **Don't add abstractions for hypothetical needs.** No plugin systems, no provider registries beyond what's already there, no config frameworks beyond the existing `config.py` layered lookup.
- **Don't add dependencies casually.** Current core deps: `python-pptx`, `Pillow` (OpenAI is called via `urllib` against the Responses API — no SDK). PyInstaller lives only in the optional `[packaging]` extra, never in core deps. Adding anything else needs justification.
- **Don't write new docs unless asked.** README, `docs/`, `packaging/DEPLOY.md`, and this file are sufficient.

## Things that are fair game

- Bug fixes within a stage's existing logic.
- Adding new fields to existing dataclasses (additive only).
- Tightening existing tests, adding new tests.
- Tuning prompts in `openai_search.py`.
- Improving image validation, source dedup, audit checks.
- Making hardcoded values configurable via env var (e.g., the model name in `openai_search.py`).
- Improvements within `src/comp_agent/config.py` (layered config/secrets resolution: env → `COMP_AGENT_CONFIG` → app-adjacent file → shared UNC config → repo `.env`), as long as precedence stays documented, env always wins, resolution never raises or hangs on bad/unreachable files, and key VALUES are never logged, printed, or returned in reports.
- Improvements within the desktop launcher (`src/comp_agent/app.py`) and the PyInstaller spec/docs under `packaging/` (built with the optional `[packaging]` extra).
- Additive extensions to the local UI JSON API (e.g., the existing `/api/preflight` and `/api/settings`) — never rename existing endpoints or JSON fields.

## Things that need user approval first

- Any change to `CompAppStages` method names, signatures, or return-dict keys.
- Any change to the JSON/CSV file names or schemas listed in `docs/stage_contracts.md`.
- Adding a new dependency (core or optional).
- Replacing the HTTP server, adding auth, adding a database.
- Changing the PPTX layout, color scheme, or branding. Branding stays Pelli Clarke Partners for now — do not white-label or touch the logo/colors in `deck.py` (the freeze-safe `LOGO_PATH` lookup is the only sanctioned `deck.py` change, already made).
- Renaming the `comp_agent` package, console scripts, env-var prefixes, or the distribution name.
- Building the hosted web service (the `POST /jobs` shape in the docs is the documented future seam, not a task).

## Environment notes

- Python 3.10+, Windows + bash via Claude Code (use forward slashes).
- Config/secrets resolve through the layered lookup in `src/comp_agent/config.py`: process env → `COMP_AGENT_CONFIG` file → app-adjacent `comp_agent.config.json`/`comp_agent.env` → shared UNC config (`\\datafiles\reference\28_AI\_AI AGENTS\CompAgent\`, timeout-guarded) → repo-local `.env`. For dev, `OPENAI_API_KEY` in `.env` still works (see `.env.example`). Live search is optional — code falls back to placeholders when absent. Never log or print the key.
- Tests run with `pytest`; do not skip or `xfail` to make a change land.
- Desktop packaging: `pip install -e .[packaging]`, then build `packaging/comp_agent.spec` per `packaging/DEPLOY.md`. Onedir, not onefile.
