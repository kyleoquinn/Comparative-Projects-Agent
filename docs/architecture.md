# Architecture Notes

Comp Agent is a **standalone product**. The current phase is an internal
desktop distribution: a packaged (PyInstaller onedir) app on the office share
that each architect launches with one click. A **hosted web service is a later
phase** — the seams (stage API, file-based artifacts, local HTTP endpoints)
are kept stable so that hosted mode is additive, not a rewrite.

## Current Runtime Model (internal desktop distribution)

```text
Architect
  -> shortcut on R:\28_AI\Comparative Projects Deck Generator (share holds the onedir app folder)
  -> launcher (comp_agent/app.py)
       -> layered config resolution (comp_agent/config.py):
          process env -> COMP_AGENT_CONFIG file -> app-adjacent config
          -> shared UNC config (\\datafiles\reference\28_AI\Comparative Projects Deck Generator\CompAgent\)
          -> repo-local .env (dev fallback)
       -> local server on 127.0.0.1 (stdlib ThreadingHTTPServer, free-port fallback)
       -> default browser opens the built-in single-page UI
  -> OpenAI API and public web sources (live search)
  -> outputs written to the user-chosen folder (e.g. a mapped drive)
```

Key properties of this model:

- **Not a shared server.** The app bytes live on the share, but the exe runs
  in each user's own Windows session and binds `127.0.0.1` on *their* PC.
  Multiple architects run concurrently with zero interaction.
- **No secret in the install folder or binary.** The OpenAI key lives in one
  shared config file on the office drive, referenced by UNC path (never the
  `X:` drive letter, which varies per machine). Rotating the key means editing
  that one file; every machine picks it up on next launch.
- **Updating = replacing the app folder on the share.** No per-machine
  reinstall.

See `packaging/DEPLOY.md` for the build, share layout, and IT checklist.

## Future Phase: Hosted Web Service (deferred, not built)

Later, the same pipeline can be exposed as a hosted service that a web client
calls over HTTP. Nothing in the current codebase should couple to
single-user desktop assumptions in a way that blocks this (e.g. the shared
config path lives in the config layer, not in stage logic). Building the
hosted service — including auth or a database — is out of scope and needs
explicit approval.

```text
User
  -> Web client UI
  -> Hosted Comp Agent API
  -> OpenAI API and public web sources
  -> Stored outputs
  -> Output links in the client
```

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

Discovery is lightweight and meant to support candidate selection. Approved
comps then receive the heavier evidence-building work before deck generation.
All stage state is file-based under `projects/<slug>/` — see
`docs/stage_contracts.md` (authoritative).

## API Boundary

Today the "client" is the built-in browser UI served by `ui.py`; in the future
hosted phase it would be a separate web client. Either way, the boundary is
the same:

The client side owns:

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

## Local Support Endpoints

Beyond the job lifecycle, the local UI server exposes support endpoints for
the desktop experience (all additive; shapes in `docs/api_contract.md`):

- `GET /api/preflight` — secrets-free startup health check: is the OpenAI key
  resolved, which config layer supplied it, is the shared config reachable,
  plus a friendly error object for the UI banner when something is wrong.
- `GET /api/settings` / `POST /api/settings` — persist per-user preferences
  (output folder, live-search toggle) in a local settings file so architects
  don't re-enter them each run.

## Future Production API Shape

The recommended production contract for the hosted phase is:

```http
POST /jobs
GET  /jobs/{job_id}
POST /jobs/{job_id}/approve
GET  /jobs/{job_id}/outputs
```

This is the documented future shape only — it is intentionally not built in
this phase.
