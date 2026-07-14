# Comp Agent

Comp Agent is a standalone tool for producing client-facing comparative project decks. It discovers candidate comps, asks for approval, builds evidence packages for approved comps, validates image packages, and generates a standardized PowerPoint deck plus supporting JSON artifacts.

The current distribution model is an **internal desktop app**: a packaged (PyInstaller onedir) build lives on the office share (`R:\28_AI\Comparative Projects Deck Generator`), an architect double-clicks one shortcut, the launcher resolves the shared OpenAI key config over the network (UNC path), starts a local web server on `127.0.0.1`, and opens the browser to the built-in UI. Each user runs their own instance on their own machine — there is no hosted server.

```text
Architect clicks shortcut on the share
  -> launcher resolves config/key (shared UNC config, layered lookup)
  -> local server starts on 127.0.0.1 (free-port fallback)
  -> browser opens the built-in UI
  -> user fills the form, approves comps, picks an output folder
  -> deck + JSON artifacts written to the chosen folder
```

A **hosted web service is a planned later phase**, not part of this repo today. The stage API, file-based artifacts, and local HTTP endpoints are kept stable so that hosted mode is an additive future effort — see "Future Production API" below and `docs/architecture.md`.

## Current Capabilities

- Live OpenAI web search for candidate comp discovery.
- User-defined comps with dedupe against live results.
- Approval workflow before deeper research.
- Mandatory per-comp enrichment for approved comps.
- Per-comp repair for missing or weak data.
- Targeted image validation and repair.
- Final deck audit and capped field-level repairs.
- Standardized `Comparative Projects` PPTX deck.
- JSON outputs for deck data, strategy, source metadata, diligence notes, audit results, and repair results.

## Run As Desktop App

The desktop launcher is the packaged entry point (and works from a dev checkout too):

```powershell
comp-agent-app            # or: python -m comp_agent.app
```

It resolves config/secrets via the layered lookup in `src/comp_agent/config.py` (process env → `COMP_AGENT_CONFIG` file → app-adjacent config → shared UNC config → repo `.env`), picks a port (8765 preferred, automatic free-port fallback), starts the local server, and opens the browser. Flags:

```text
--version       Print "Comp Agent <version>" and exit.
--no-browser    Do not auto-open the browser (smoke tests, headless QA).
--port          Preferred port (falls back to a free port if taken).
--output-root   Default project workspace root (the UI still requires an explicit output folder).
```

To build and deploy the packaged app (PyInstaller onedir, share layout, shortcut, key-config setup, IT checklist), see [packaging/DEPLOY.md](packaging/DEPLOY.md). PyInstaller is an optional extra, not a core dependency:

```powershell
pip install -e .[packaging]
```

## Local Setup (development)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
Copy-Item .env.example .env
```

Add your OpenAI API key to `.env` (the dev fallback — packaged installs read the shared config instead; see `docs/local_dev.md` for the full resolution order):

```text
OPENAI_API_KEY=sk-...
COMP_AGENT_LIVE_SEARCH=1
```

Start the local WebUI directly (without the launcher):

```powershell
python -m comp_agent.cli ui --host 127.0.0.1 --port 8765 --output-root projects_ui
```

Then open:

```text
http://127.0.0.1:8765
```

## Documentation

- [API contract](docs/api_contract.md) — the local HTTP API and the future hosted shape
- [Output contract](docs/output_contract.md)
- [Local development](docs/local_dev.md) — setup, config resolution order, env vars
- [Architecture notes](docs/architecture.md)
- [Stage contracts](docs/stage_contracts.md) — authoritative stage/file contracts
- [Build & deploy](packaging/DEPLOY.md) — packaged desktop distribution

## Current Local API

The built-in WebUI exposes:

```http
GET  /api/preflight
GET  /api/settings
POST /api/settings
POST /api/discover/start
GET  /api/jobs/{job_id}
POST /api/approve/start
POST /api/select-output-folder
```

See [docs/api_contract.md](docs/api_contract.md) for request/response shapes.

## Future Production API

When Comp Agent is later offered as a hosted web service, the recommended shape is:

```http
POST /jobs
GET  /jobs/{job_id}
POST /jobs/{job_id}/approve
GET  /jobs/{job_id}/outputs
```

This is documentation of the future seam, not something built today. A future hosted client would collect project inputs, start discovery, poll progress, render candidate comps for approval, submit selected comp IDs, and display output links after deck generation. Comp Agent owns research, enrichment, repair, image handling, audit, and PPTX generation.

## Output Structure

Each run writes to:

```text
{output_root}/{project_slug}/
  inputs/
    project_brief.json
  outputs/
    comp_study_deck.pptx
    data/
    graphics/
    images/
    json/
    sources/
    working/
```

The PPTX is intentionally the only file directly inside `outputs/`. Machine-readable artifacts live under `outputs/json/` and `outputs/data/`.

## Test

```powershell
pytest
```

## Repository Notes

- `.env`, `comp_agent.config.json`, and `comp_agent.env` are ignored and should never be committed. Real keys live only in the shared config on the office drive (or a local `.env` for dev).
- Generated run folders such as `projects/` and `projects_ui/` are ignored.
- This repo is the whole product for the current phase: pipeline, local UI, launcher, and packaging. A hosted web service (and any separate client for it) is a later, additive phase.
