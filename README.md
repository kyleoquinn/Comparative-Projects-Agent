# Comp Agent

Comp Agent is a modular backend agent for producing client-facing comparative project decks. It discovers candidate comps, asks for approval, builds evidence packages for approved comps, validates image packages, and generates a standardized PowerPoint deck plus supporting JSON artifacts.

The intended product architecture is:

```text
Frontend UI
  -> calls hosted Comp Agent API
  -> user approves comps
  -> Comp Agent runs research/deck pipeline
  -> frontend displays progress and output links
```

This repo should be treated as the reference implementation for the Comp Agent backend. A separate frontend repo can use the docs here to understand which inputs to collect, how to call the agent, what progress states to show, and where output artifacts are written.

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

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
Copy-Item .env.example .env
```

Add your OpenAI API key to `.env`:

```text
OPENAI_API_KEY=sk-...
COMP_AGENT_LIVE_SEARCH=1
```

Start the temporary local WebUI:

```powershell
python -m comp_agent.cli ui --host 127.0.0.1 --port 8765 --output-root projects_ui
```

Then open:

```text
http://127.0.0.1:8765
```

## Frontend Integration Docs

- [API contract](docs/api_contract.md)
- [Comp agent input packet](docs/comp_agent_input_packet.md)
- [Frontend inputs](docs/frontend_inputs.md)
- [Output contract](docs/output_contract.md)
- [Local development](docs/local_dev.md)
- [Architecture notes](docs/architecture.md)

Example frontend payloads:

- [Discovery request](examples/frontend_discovery_request.json)
- [Approval request](examples/frontend_approval_request.json)

## Current Development API

The temporary WebUI currently exposes:

```http
POST /api/discover/start
GET  /api/jobs/{job_id}
POST /api/approve/start
```

For production, the recommended shape is:

```http
POST /jobs
GET  /jobs/{job_id}
POST /jobs/{job_id}/approve
GET  /jobs/{job_id}/outputs
```

The frontend should collect project inputs, start discovery, poll progress, render candidate comps for approval, submit selected comp IDs, and display output links after deck generation. The Comp Agent owns research, enrichment, repair, image handling, audit, and PPTX generation.

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

- `.env` is ignored and should never be committed.
- Generated run folders such as `projects/` and `projects_ui/` are ignored.
- This repo is backend-focused. The production frontend can live in a separate repo and call this agent over HTTP.
