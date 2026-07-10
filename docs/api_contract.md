# Comp Agent API Contract

Comp Agent is a standalone tool. In the current phase it ships as an internal
desktop app: the packaged launcher starts a local HTTP server on `127.0.0.1`
and the built-in browser UI is the client. The endpoints below are that local
API. They are also the seam a future hosted web service will reuse, so treat
the shapes as stable: extend additively, never rename endpoints or JSON
fields.

The client (today the built-in UI; later a web client) collects inputs, starts
jobs, polls progress, handles comp approval, and shows output links. The comp
agent owns discovery, OpenAI web search, enrichment, repair, image collection,
PPTX generation, and JSON artifacts.

## Current Local API

The local server is started by the desktop launcher (`comp-agent-app` /
`python -m comp_agent.app`, with automatic free-port fallback) or directly:

```powershell
python -m comp_agent.cli ui --host 127.0.0.1 --port 8765 --output-root projects_ui
```

Base URL:

```text
http://127.0.0.1:8765
```

### Preflight Check

```http
GET /api/preflight
```

Secrets-free startup health check. The UI calls it on load and renders
`friendly_error` (when present) as a banner. Key VALUES never appear in this
payload — only key names and source paths.

```json
{
  "ok": true,
  "openai_key_present": true,
  "openai_reachable": true,
  "key_source": "shared",
  "share_reachable": true,
  "default_output_root": "C:\\Users\\name\\Documents\\Comp Packages",
  "layers": [
    {
      "layer": "shared",
      "source": "\\\\datafiles\\reference\\28_AI\\_AI AGENTS\\CompAgent\\comp_agent.config.json",
      "status": "loaded",
      "format": "json",
      "keys_set": ["OPENAI_API_KEY"],
      "keys_already_in_env": []
    }
  ],
  "friendly_error": null
}
```

- `ok` — key resolved AND (when checked) the OpenAI API is reachable.
- `openai_key_present` — whether `OPENAI_API_KEY` resolved.
- `openai_reachable` — `true`/`false` from an unauthenticated reachability
  probe of `api.openai.com` (the key never leaves the process); `null` when
  skipped because no key resolved.
- `key_source` — best-effort name of the config layer that supplied the key:
  `"env"`, `"explicit"`, `"app_dir"`, `"shared"`, `"dotenv"`, or `null`.
- `share_reachable` — `true`/`false`, or `null` when the shared layer was not
  probed (e.g. skipped because `COMP_AGENT_CONFIG` overrides it). `false`
  covers both a hung probe (timeout) and a fast network failure (the common
  off-VPN DNS case).
- `layers` — the per-layer resolution report from `comp_agent.config`
  (`status` is `loaded` | `missing` | `unreachable` | `timeout` |
  `parse-error` | `skipped`). Preflight resolution is read-only: it never
  writes env vars into the running process.
- `friendly_error` — `null` when healthy; otherwise
  `{"headline": "...", "detail": "..."}` with non-technical copy
  ("Can't Reach the Shared Key Config" for VPN/share problems,
  "No OpenAI Key Found" when no layer carries the key,
  "Can't Reach OpenAI" when the key resolved but the API is blocked).

### Per-User Settings

```http
GET /api/settings
```

```json
{
  "settings": {
    "output_root": "D:\\Comp Packages",
    "live_search": true
  },
  "path": "C:\\Users\\name\\AppData\\Local\\CompAgent\\settings.json"
}
```

`settings` may be `{}` or omit either key when nothing has been saved yet;
corrupt or missing settings files silently yield `{}`.

```http
POST /api/settings
Content-Type: application/json
```

Request body (both keys optional; unknown keys and wrong types are dropped):

```json
{
  "output_root": "D:\\Comp Packages",
  "live_search": true
}
```

Response (merge preserves previously saved keys not present in the request;
write failures return `"ok": false` instead of an HTTP error):

```json
{
  "ok": true,
  "settings": {
    "output_root": "D:\\Comp Packages",
    "live_search": true
  },
  "path": "C:\\Users\\name\\AppData\\Local\\CompAgent\\settings.json"
}
```

The UI uses these to pre-fill the output folder (saved value first, then the
per-user Documents default from preflight) and to restore the live-search
toggle.

### Start Candidate Discovery

```http
POST /api/discover/start
Content-Type: application/json
```

Request body:

```json
{
  "project_name": "200 Vesey Test Study",
  "address": "200 Vesey Street, New York, NY",
  "program_type": "office repositioning",
  "geography": "New York, NY",
  "max_comps": 7,
  "comp_types": "lobby repositioning, podium renovation",
  "amenity_priorities": "arrival experience, tenant amenities, public realm",
  "radius_miles": 3,
  "time_horizon_years": 8,
  "live_search": true,
  "user_defined_comps": "660 Fifth Avenue | New York, NY | Office lobby precedent\n343 Madison | New York, NY | Office tower precedent",
  "auto_approve_user_comps": false,
  "output_root": "C:\\Comp Outputs"
}
```

`output_root` is **required and must be an absolute path** (drive-letter or
UNC). A missing or relative value fails the job before any work starts, with
an "Output Folder Problem" `friendly_error`.

Response:

```json
{
  "job_id": "abc123",
  "status_url": "/api/jobs/abc123"
}
```

### Poll Job Status

```http
GET /api/jobs/{job_id}
```

Response while running:

```json
{
  "job_id": "abc123",
  "kind": "discover",
  "status": "running",
  "message": "Searching comparable projects",
  "percent": 30,
  "elapsed_seconds": 21.4,
  "result": null,
  "error": ""
}
```

Response when complete:

```json
{
  "job_id": "abc123",
  "kind": "discover",
  "status": "complete",
  "message": "Complete",
  "percent": 100,
  "elapsed_seconds": 84.2,
  "result": {
    "brief": {},
    "output_root": "C:\\Comp Outputs",
    "paths": {},
    "candidates": [],
    "source_log": []
  },
  "error": ""
}
```

### Candidate Object

The discovery result returns `candidates`. The client renders these for user approval.

```json
{
  "comp_id": "200-vesey-test-study-660-fifth-avenue",
  "comp_name": "660 Fifth Avenue",
  "location": "New York, NY",
  "comp_type": "adaptive reuse",
  "relevance_score": 92,
  "status": "source_snapshot",
  "candidate_source": "live_search",
  "user_note": "",
  "known_attributes": {
    "program_type": "office repositioning",
    "presentation_takeaway": "Concise reason this comp may fit.",
    "developer_owner": "Owner if available",
    "architect_designer": "Designer if available"
  },
  "missing_attributes": [],
  "source_notes": []
}
```

Approval sends the selected `comp_id` values.

### Approve Candidates And Generate Deck

```http
POST /api/approve/start
Content-Type: application/json
```

Request body:

```json
{
  "brief": {
    "project_name": "200 Vesey Test Study",
    "address": "200 Vesey Street, New York, NY",
    "program_type": "office repositioning",
    "geography": "New York, NY",
    "max_comps": 7,
    "comp_types": ["lobby repositioning", "podium renovation"],
    "amenity_priorities": ["arrival experience", "tenant amenities", "public realm"],
    "radius_miles": 3,
    "time_horizon_years": 8,
    "filters": {}
  },
  "comp_ids": [
    "200-vesey-test-study-660-fifth-avenue",
    "200-vesey-test-study-1271-avenue-of-the-americas"
  ],
  "output_root": "C:\\Comp Outputs",
  "live_search": true
}
```

`output_root` is required and absolute, same as discovery.

The job status endpoint is the same as discovery. When complete, `result.paths` contains output files (absolute, rooted under the chosen output folder):

```json
{
  "comp_study_deck": "C:\\Comp Outputs\\project-slug\\outputs\\comp_study_deck.pptx",
  "deck_data": "C:\\Comp Outputs\\project-slug\\outputs\\json\\deck_data.json",
  "deck_strategy": "C:\\Comp Outputs\\project-slug\\outputs\\json\\deck_strategy.json",
  "approved_comps_normalized": "C:\\Comp Outputs\\project-slug\\outputs\\json\\approved_comps_normalized.json",
  "source_metadata": "C:\\Comp Outputs\\project-slug\\outputs\\json\\source_metadata.json",
  "diligence_notes": "C:\\Comp Outputs\\project-slug\\outputs\\json\\diligence_notes.json",
  "deck_audit": "C:\\Comp Outputs\\project-slug\\outputs\\json\\deck_audit.json",
  "field_repair_tasks": "C:\\Comp Outputs\\project-slug\\outputs\\json\\field_repair_tasks.json",
  "field_repair_results": "C:\\Comp Outputs\\project-slug\\outputs\\json\\field_repair_results.json",
  "image_manifest": "C:\\Comp Outputs\\project-slug\\outputs\\json\\image_manifest.json"
}
```

## Future Production API (hosted phase, not built)

The hosted web service is a deferred later phase. When it is built, the preferred connector shape is:

```http
POST /jobs
GET  /jobs/{job_id}
POST /jobs/{job_id}/approve
GET  /jobs/{job_id}/outputs
```

A future web client can support many agents if each agent follows this pattern:

```ts
type AgentConnector = {
  id: string;
  displayName: string;
  baseUrl: string;
  startJobPath: string;
  approvePath: string;
  statusPath: string;
  outputsPath: string;
};
```

Example:

```ts
export const compStudyAgent = {
  id: "comp-study",
  displayName: "Comparative Projects",
  baseUrl: process.env.COMP_AGENT_BASE_URL,
  startJobPath: "/jobs",
  approvePath: "/jobs/:jobId/approve",
  statusPath: "/jobs/:jobId",
  outputsPath: "/jobs/:jobId/outputs"
};
```

## Client Responsibilities (today: the built-in UI; later: a web client)

- Collect project inputs.
- Start discovery.
- Poll status and show progress.
- Render candidate comps with checkboxes.
- Send approved comp IDs.
- Poll deck generation status.
- Show output links and error messages.

## Comp Agent Responsibilities

- Run OpenAI live web search.
- Deduplicate user-defined and discovered comps.
- Enrich every approved comp.
- Repair missing/weak data fields.
- Validate image packages.
- Build normalized deck JSON.
- Generate PPTX.
- Save artifacts.
- Expose progress and output paths.

