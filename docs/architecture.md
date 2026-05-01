# Architecture Notes

The Comp Agent should be hosted as a backend service. The frontend does not need to run the research pipeline directly; it only needs to call the service and display progress/results.

## Recommended Repo Layout Across Products

Use one repo per backend agent plus one frontend repo:

```text
frontend-ui/
comp-agent/
future-agent-a/
future-agent-b/
```

This keeps each agent independently testable and deployable while allowing the frontend to present them as one product.

## Runtime Model

```text
User
  -> Frontend UI
  -> Comp Agent API
  -> OpenAI API and public web sources
  -> Stored outputs
  -> Frontend output links
```

The frontend and backend may live on the same server, but the cleanest integration is still an HTTP API. A web address gives each agent a stable interface, works locally and remotely, and lets future agents be added without coupling their Python internals to the UI codebase.

## Current Pipeline

```text
Discovery
  -> Approval
  -> Per-comp enrichment
  -> Per-comp repair
  -> Image validation and image repair
  -> Deck data normalization
  -> Final deck audit
  -> Targeted field repair
  -> PPTX generation
```

Discovery is lightweight and meant to support candidate selection. Approved comps then receive the heavier evidence-building work before deck generation.

## Connector Boundary

The frontend owns:

- input forms
- job creation calls
- polling and progress display
- candidate approval UI
- output links

The Comp Agent owns:

- OpenAI calls
- comp dedupe
- enrichment and repair
- image gathering and validation
- source metadata
- normalized deck JSON
- PPTX generation
- internal diligence notes

## Production API Shape

The recommended production contract is:

```http
POST /jobs
GET  /jobs/{job_id}
POST /jobs/{job_id}/approve
GET  /jobs/{job_id}/outputs
```

This allows every future agent to expose the same basic lifecycle while keeping agent-specific input schemas separate.
