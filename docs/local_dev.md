# Local Development

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
Copy-Item .env.example .env
```

Edit `.env`:

```text
OPENAI_API_KEY=sk-...
COMP_AGENT_LIVE_SEARCH=1
```

## Start The Temporary WebUI

```powershell
python -m comp_agent.cli ui --host 127.0.0.1 --port 8765 --output-root projects_ui
```

Open:

```text
http://127.0.0.1:8765
```

The temporary WebUI is for testing the agent, visualizing inputs, approving comps, and inspecting outputs. It is not intended to be the final product UI.

## Run Tests

```powershell
pytest
```

## Useful CLI Commands

Create an example brief:

```powershell
python -m comp_agent.cli init --output examples/project_brief.json
```

Run discovery:

```powershell
python -m comp_agent.cli discover --brief examples/project_brief.json --output-root projects
```

Run the approval/deck pipeline after approving comps:

```powershell
python -m comp_agent.cli approve --brief examples/project_brief.json --output-root projects --comp-id PROJECT_COMP_ID
python -m comp_agent.cli research --brief examples/project_brief.json --output-root projects
python -m comp_agent.cli format --brief examples/project_brief.json --output-root projects
python -m comp_agent.cli outputs --brief examples/project_brief.json --output-root projects
python -m comp_agent.cli audit --brief examples/project_brief.json --output-root projects
```

## Environment Variables

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Required for live OpenAI search. |
| `COMP_AGENT_LIVE_SEARCH` | Set to `1` to enable live search. |
| `COMP_AGENT_OPENAI_MODEL` | OpenAI model used by the provider. |
| `COMP_AGENT_OPENAI_TIMEOUT` | Timeout for OpenAI calls. |
| `COMP_AGENT_OPENAI_RETRIES` | Retry count for OpenAI calls. |
| `COMP_AGENT_RESEARCH_CONCURRENCY` | Number of approved comps to enrich in parallel. Default is 3. |
| `COMP_AGENT_FIELD_REPAIR_LIMIT` | Cap for final auditor field repair calls. |

## Git Safety

The repo ignores:

- `.env`
- `projects/`
- `projects_ui/`
- logs
- temp PPTX files
- Python caches and package metadata

Generated output folders should stay out of source control.
