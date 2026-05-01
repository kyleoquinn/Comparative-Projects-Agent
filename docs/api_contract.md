# Comp Agent API Contract

This repo is the backend service for the Comparative Projects / Comp Study Deck agent.

The frontend should treat the comp agent as a hosted backend service. The frontend collects inputs, starts jobs, polls progress, handles comp approval, and shows output links. The comp agent owns discovery, OpenAI web search, enrichment, repair, image collection, PPTX generation, and JSON artifacts.

## Current Local Development API

The current development server is started with:

```powershell
python -m comp_agent.cli ui --host 127.0.0.1 --port 8765 --output-root projects_ui
```

Base URL:

```text
http://127.0.0.1:8765
```

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
  "output_root": "projects_ui"
}
```

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
    "output_root": "projects_ui",
    "paths": {},
    "candidates": [],
    "source_log": []
  },
  "error": ""
}
```

### Candidate Object

The discovery result returns `candidates`. The frontend should render these for user approval.

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

Frontend approval should send selected `comp_id` values.

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
  "output_root": "projects_ui",
  "live_search": true
}
```

The job status endpoint is the same as discovery. When complete, `result.paths` contains output files.

Important returned paths:

```json
{
  "comp_study_deck": "projects_ui/project-slug/outputs/comp_study_deck.pptx",
  "deck_data": "projects_ui/project-slug/outputs/json/deck_data.json",
  "deck_strategy": "projects_ui/project-slug/outputs/json/deck_strategy.json",
  "approved_comps_normalized": "projects_ui/project-slug/outputs/json/approved_comps_normalized.json",
  "source_metadata": "projects_ui/project-slug/outputs/json/source_metadata.json",
  "diligence_notes": "projects_ui/project-slug/outputs/json/diligence_notes.json",
  "deck_audit": "projects_ui/project-slug/outputs/json/deck_audit.json",
  "field_repair_tasks": "projects_ui/project-slug/outputs/json/field_repair_tasks.json",
  "field_repair_results": "projects_ui/project-slug/outputs/json/field_repair_results.json"
}
```

## Recommended Production API

The current local UI endpoints are intentionally temporary. For production, the preferred connector shape is:

```http
POST /jobs
GET  /jobs/{job_id}
POST /jobs/{job_id}/approve
GET  /jobs/{job_id}/outputs
```

The frontend can support many agents if each agent follows this pattern:

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

## Frontend Responsibilities

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

