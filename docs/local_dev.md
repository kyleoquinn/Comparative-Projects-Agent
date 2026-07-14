# Local Development

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
Copy-Item .env.example .env
```

Edit `.env` (the dev fallback in the config resolution order below):

```text
OPENAI_API_KEY=sk-...
COMP_AGENT_LIVE_SEARCH=1
```

## Start The Local WebUI

Directly (dev workflow):

```powershell
python -m comp_agent.cli ui --host 127.0.0.1 --port 8765 --output-root projects_ui
```

Or via the desktop launcher, which resolves config, picks a free port, and
auto-opens the browser (this is what the packaged app runs):

```powershell
comp-agent-app --no-browser
# or: python -m comp_agent.app
```

Launcher flags: `--version`, `--no-browser`, `--port`, `--output-root`.

Open:

```text
http://127.0.0.1:8765
```

The WebUI is the product UI for the internal desktop distribution: preflight
health check, comp approval, output-folder selection, deck generation. A
hosted web service is a later phase.

## Config Resolution Order

Startup config/secrets are resolved by `src/comp_agent/config.py`
(`load_config()` / `resolve_config()`), called by both the CLI and the
launcher. Layered precedence, **first hit wins per key**:

1. **Process environment variables** — never overwritten; env always wins.
2. **`COMP_AGENT_CONFIG`** — env var pointing at an explicit config file
   (JSON or `.env` format, auto-detected). When set, it **replaces** the
   shared network default lookup (layer 4 is reported as skipped).
3. **App-adjacent config** — `comp_agent.config.json`, then
   `comp_agent.env`, next to the app: the executable directory when frozen
   under PyInstaller, otherwise the current working directory.
4. **Shared office default (UNC path)** —
   `\\datafiles\reference\28_AI\Comparative Projects Deck Generator\API Key`, then
   `comp_agent.env` beside it. Always the UNC form, never the `X:` drive
   letter. Network reads run with a 2.5-second timeout so an unreachable
   share can never hang startup (on timeout the layer is abandoned and
   reported as unreachable).
5. **Repo-local `.env`** — the dev fallback (this is what `pip install -e .`
   development uses).

Resolved values are written into `os.environ` only for keys not already set,
so `openai_search.py` / `stages.py` keep reading env vars exactly as before.
Missing, malformed, or unreachable files never raise — resolution degrades
gracefully and reports the failure. The resolver returns a **secrets-free**
report (key names and source paths only, never values) that powers the UI
preflight.

JSON config files are a flat object of string keys/values:

```json
{"OPENAI_API_KEY": "sk-...", "COMP_AGENT_LIVE_SEARCH": "1"}
```

`cli.load_dotenv()` still exists as a thin backwards-compatible shim over
`load_config()`.

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
| `COMP_AGENT_CONFIG` | Optional path to an explicit config file (JSON or `.env` format); overrides the shared-drive default lookup. |
| `COMP_AGENT_LIVE_SEARCH` | Set to `1` to enable live search. |
| `COMP_AGENT_OPENAI_MODEL` | OpenAI model used by the provider. |
| `COMP_AGENT_OPENAI_TIMEOUT` | Timeout for OpenAI calls. |
| `COMP_AGENT_OPENAI_RETRIES` | Retry count for OpenAI calls. |
| `COMP_AGENT_RESEARCH_CONCURRENCY` | Number of approved comps to enrich in parallel. Default is 3. |
| `COMP_AGENT_FIELD_REPAIR_LIMIT` | Cap for final auditor field repair calls. |

All of these (except `COMP_AGENT_CONFIG` itself) can come from any layer of
the config resolution order above instead of the process environment.

## Local Support Endpoints

Beyond the job endpoints (`/api/discover/start`, `/api/jobs/{id}`,
`/api/approve/start`, `/api/select-output-folder`), the UI server exposes:

- `GET /api/preflight` — secrets-free startup health check (key resolved?
  which layer? share reachable?) plus a `friendly_error` object for the UI
  banner. Full shape in `docs/api_contract.md`.
- `GET /api/settings` / `POST /api/settings` — per-user persisted settings
  (`output_root`, `live_search`), stored at
  `%LOCALAPPDATA%\CompAgent\settings.json` (home-directory fallback when
  `LOCALAPPDATA` is unset). Unknown keys and wrong types are dropped; corrupt
  files silently yield `{}`.

The UI calls both on load: it pre-fills the output folder (saved setting,
else the `Documents\Comp Packages` default), restores the live-search toggle,
and shows a friendly banner if the key/share preflight fails.

## Packaging

To build the desktop app (PyInstaller onedir), install the optional extra and
follow `packaging/DEPLOY.md`:

```powershell
pip install -e .[packaging]
python -m PyInstaller packaging/comp_agent.spec --noconfirm
```

PyInstaller is a build tool only — it is not a core runtime dependency.

## Git Safety

The repo ignores:

- `.env`
- `comp_agent.config.json` and `comp_agent.env` (local config files — real
  keys live only on the office share)
- `projects/`
- `projects_ui/`
- logs
- temp PPTX files
- Python caches and package metadata

Generated output folders should stay out of source control. Never commit a
real key in any config file.
