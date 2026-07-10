# Comp Agent → Standalone Product: Migration Plan

**Author:** Planning pass (Opus 4.8) · **Implementer:** Fable 5 · **Date:** 2026-07-10
**Base commit:** `47e49e8` ("Add per-scope comp counts, friendly UI errors, and
geographic guardrails") — the current tip of `main`. This plan was refreshed
against it; start work from here, not any earlier copy.

This is an implementation plan, not a spec change. Read `CLAUDE.md` and
`docs/stage_contracts.md` first — **the stage boundaries, JSON/CSV file
contracts, `models.py` wire format, and the full test suite (51 tests) in
`tests/` all still hold.** Nothing in this plan authorizes
breaking them. Where a step would touch a contract, it says so explicitly and
routes back to Kyle.

---

## 1. What changed (trajectory)

Comp Agent is no longer a bolt-on backend for the Pelli Agent. It becomes a
**standalone product** — eventually something architects use on their own to
generate comparative-projects ("comp") packages, potentially marketed
independently of Pelli.

**This phase is narrower than that end state.** The goal now is: **an internal,
self-serve tool the office can actually run**, with the seams kept clean so a
hosted web service is a later addition rather than a rewrite.

### Decisions locked with Kyle (do not re-litigate)

| Topic | Decision |
|---|---|
| Deployment target (this phase) | **Internal office use.** Not the public web service yet. |
| Hosting | **None available.** A server breach means the mainframe is off-limits; only files copied to **mapped drives** can be distributed. |
| Branding | **Keep Pelli Clarke Partners branding for now.** Do *not* white-label or rip out the logo/colors in `deck.py`. Revisit before any external marketing. |
| API key handling | **One shared office OpenAI key, delivered via a config file on a mapped drive** — *not* bundled into the app binary. Key must not be casually exposed to everyone in the office. |
| Web service | **Deferred.** Preserve the current stage/HTTP seam so it's an additive future effort. |

---

## 2. Current state (grounding for the implementer)

- Python 3.10+ package `comp_agent` under `src/`. Deps: `python-pptx`, `Pillow`,
  `openai` (implicit via Responses API).
- **Entry points:** `comp-agent` console script → `comp_agent.cli:main`.
  Subcommands: `init`, `ui`, `run`, `discover`, `approve`, `research`, `format`,
  `outputs`, `audit`, `poc`.
- **`ui` command** (`src/comp_agent/ui.py`) starts a stdlib
  `ThreadingHTTPServer` on `127.0.0.1:8765`, serving one embedded single-page app
  (`INDEX_HTML` string) plus a small JSON API
  (`/api/discover/start`, `/api/jobs/{id}`, `/api/approve/start`,
  `/api/select-output-folder`). Jobs run on background threads; the browser polls.
- **Pipeline:** `CompAppStages` (`stages.py`) → `discover → approve → research →
  format_outputs → generate_outputs → audit`, all file-based under
  `projects/<slug>/`. Output PPTX + JSON artifacts land in `<output_root>/<slug>/outputs/`.
- **Secrets/config today:** `cli.py:load_dotenv()` reads a repo-local `.env`;
  `openai_search.py` reads `OPENAI_API_KEY`, `COMP_AGENT_LIVE_SEARCH`,
  `COMP_AGENT_OPENAI_MODEL` (default `gpt-5`), `COMP_AGENT_OPENAI_TIMEOUT`,
  `COMP_AGENT_OPENAI_RETRIES`; `stages.py` reads `COMP_AGENT_RESEARCH_CONCURRENCY`
  and `COMP_AGENT_FIELD_REPAIR_LIMIT`. `ui.py` also toggles
  `COMP_AGENT_LIVE_SEARCH` per request.
- **Output folder** is already required and user-chosen at runtime (absolute path
  enforced server-side by `_require_output_root()` and client-side by
  `validateOutputRoot()`; tkinter folder picker in the UI). This already supports
  writing decks straight to a mapped drive.
- **Friendly errors already exist (base `47e49e8`).** `ui.py:_classify_error()`
  maps exceptions/warnings to a `{headline, detail}` object, attaches it to the
  job as `friendly_error`, and the UI renders it as a banner (`bannerHtml`)
  instead of a raw traceback. **WS-3 must build on this, not rebuild it.**
- **Per-scope discovery already exists (base `47e49e8`).** `models.py` has a
  `comps_per_scope` brief field; `discovery.py`/`research.py` honor per-scope
  counts; the UI has scope-count inputs with a `SCOPE_TOTAL_CAP`. Not part of this
  migration — just don't regress it.
- **Branding** lives in `deck.py` (`LOGO_PATH` → `assets/pelli_clarke_partners_logo.jpg`,
  color constants, `_add_logo`). **Left untouched this phase.**

The architecture is already well-suited to standalone use: file-based state, a
clean stage API, a local UI that requires no external services except OpenAI.
The work is mostly **packaging, configuration/secrets, and non-technical-user
hardening** — not a rearchitecture.

---

## 3. The central problem: run without a server *and* protect one shared key

This is the crux of the whole phase, so it's stated plainly.

**The hard truth:** any program that calls OpenAI with the office key must be
able to read that key at runtime. If the program runs on an architect's PC, the
key is on that PC while it runs. **You cannot cryptographically hide a key from
the person running the software on their own machine.** Bundling it inside a
`.exe` does *not* protect it — it's recoverable with `strings`/a debugger, and it
can't be rotated without a rebuild.

So "don't expose the key to everyone" is achievable, but as an **access-control**
problem, not an encryption one. The realistic internal-office answer, matching
Kyle's decision:

1. **Externalize the key into a config file on the shared network drive**,
   separate from the app binary. **Deploy home (confirmed with Kyle):**
   `X:\28_AI\_AI AGENTS` (a mapping of `\\datafiles`). Reference the config by its
   **UNC path** (`\\datafiles\28_AI\_AI AGENTS\...`), **not** the `X:` drive
   letter — drive-letter mappings differ per user/machine and silently break;
   the UNC path is identical for everyone on VPN. The app reads it at startup.
2. **Nothing secret sits in the install folder.** The only local pointer, if any,
   is a non-secret settings file naming *where* the config lives (the UNC path).
   The key itself stays on the share.
3. **Never bake the key into the packaged binary.** Rotate the key by editing one
   plain `.env`/JSON file on the share (Notepad-editable); every machine picks up
   the new value on next launch. No redeploy.
4. **Set a hard monthly usage cap on the OpenAI key** in the OpenAI dashboard, so a
   shared key can't produce runaway spend.
5. **Access control is optional, per Kyle's stance.** Whoever can *read* that
   folder effectively has the key. Kyle is fine with all VPN users having it and is
   not worried about extraction — the requirement is UX (no key entry, no loose
   key file), not hard secrecy. If the trust boundary ever tightens (contractors,
   wider VPN), the lever is restricting that one folder's read permission to an
   architect group — an IT change, no code change. Not built now.

### Recommended run model (the frictionless target)

**Packaged desktop app (PyInstaller) launched from one obvious shortcut on the
share, opening the existing local UI in the browser.** The bar Kyle set: *go to
the folder → click one thing → fill the form → pick output → get a PowerPoint.*
Concretely:

- **One entry point** at the top of `X:\28_AI\_AI AGENTS` — a Windows shortcut
  (`.lnk`) named e.g. `▶ Start Comp Agent`, custom icon, pointing at the launcher.
- **Double-click →** launcher resolves config (WS-1) → starts the local server on
  `127.0.0.1` (auto free-port) → **auto-opens the browser** to the UI so the form
  is just there.
- **No Python on the user's PC** (PyInstaller bundles it); **no server to host**;
  **no key in the binary** (read from the share).
- **It is not a shared server.** Even though the bytes live on `X:\`, the exe runs
  in each user's own Windows session and binds `127.0.0.1` on *their* PC. Multiple
  architects run at once with zero interaction; the key config is the only truly
  shared file.
- **Updating = replace the app folder on the share.** Everyone gets the new
  version on their next click; no per-machine reinstall.

This directly reconciles Kyle's constraints: packaged click-to-run app, shared key
that no one types and that lives in no loose local file.

### Upgrade path if key-on-client ever becomes unacceptable

Stand up a **minimal LAN proxy on one trusted machine** (not the breached
mainframe): a tiny local service that holds the key and forwards OpenAI calls;
client apps point at the proxy instead of holding the key. This is the *only* way
to keep the key entirely off client machines, and it reintroduces a hosted
component — so it's explicitly **out of scope for this phase** and noted only as
the escalation option. Do not build it now.

---

## 4. Workstreams

Ordered by dependency and risk. Each is scoped to survive `pytest` green and the
contracts intact.

### WS-1 — Config & secrets resolution (highest priority; enables everything else)

**Problem:** config resolution is repo-relative today (`load_dotenv(".env")` runs
from the current working directory). A packaged app copied to a mapped drive, and
run from anywhere, needs a **deterministic, documented lookup order** that finds
the shared config regardless of CWD.

**Do:**
- Introduce a single config-resolution helper (new small module, e.g.
  `src/comp_agent/config.py`) that loads settings from a **layered precedence**,
  first hit wins:
  1. Process environment variables (unchanged — preserves current behavior/tests).
  2. `COMP_AGENT_CONFIG` env var pointing at an explicit config file, if set.
  3. A config file next to the executable / on the mapped drive (documented default
     path, e.g. `./comp_agent.config.json` resolved against the app dir, then a
     configurable shared path).
  4. The existing repo-local `.env` (keep as the dev fallback).
- Support both `.env` and a JSON config file. Reuse the existing `.env` parsing in
  `cli.py:load_dotenv()` — **refactor it into the new module rather than
  duplicating**; keep a thin `load_dotenv` shim so nothing else breaks.
- Config keys are exactly the existing env vars (`OPENAI_API_KEY`,
  `COMP_AGENT_LIVE_SEARCH`, `COMP_AGENT_OPENAI_MODEL`, `COMP_AGENT_OPENAI_TIMEOUT`,
  `COMP_AGENT_OPENAI_RETRIES`, `COMP_AGENT_RESEARCH_CONCURRENCY`,
  `COMP_AGENT_FIELD_REPAIR_LIMIT`). **No new consumer code** — resolved values are
  written into `os.environ` early in startup so `openai_search.py`/`stages.py` keep
  reading env vars exactly as they do now. This keeps the change additive and the
  tests untouched.

**Guardrails:** do not change how `openai_search.py`/`stages.py` *read* config —
only change how it's *populated* at startup. Don't log the key. Don't commit any
real config. Add `comp_agent.config.json` to `.gitignore` alongside `.env`.

**Tests:** add unit tests for precedence order and file discovery (new test file,
e.g. `tests/test_config.py`). Existing tests stay green because env vars still win.

**Risk:** Low. Additive, behind a shim.

---

### WS-2 — Packaging & distribution (PyInstaller desktop app)

**Do:**
- Add a thin launcher entry point (e.g. `src/comp_agent/app.py` or a `ui --open`
  flag) that: resolves config (WS-1) → starts `run_server` on `127.0.0.1` with an
  **automatic free-port fallback** if 8765 is taken → opens the default browser
  (`webbrowser.open`) → keeps the process alive with a clear console message.
- Add a PyInstaller spec (e.g. `packaging/comp_agent.spec`) that bundles the
  package **including data files**: `assets/pelli_clarke_partners_logo.jpg` and any
  other runtime assets must be added via `datas` and resolved through a
  PyInstaller-safe path helper (handle `sys._MEIPASS`). Note: `LOGO_PATH` in
  `deck.py` uses `Path(__file__).with_name(...)`, which breaks under a frozen
  binary — add a resource-path shim so the logo still loads. **The logo image
  stays exactly as-is; only its *lookup* is made freeze-safe.**
- Confirm `tkinter` (used by the folder picker in `ui.py`) is included in the
  bundle, or gracefully degrade to manual path entry (the UI already supports
  typing a path).
- **Build `--onedir`, NOT `--onefile`.** Onefile unpacks to `%TEMP%` on every
  launch — slow over VPN and worse for antivirus. Onedir launches fast even from
  the share. Tuck the onedir output in a subfolder (e.g. `_app\`) and expose only
  the shortcut at the top of `X:\28_AI\_AI AGENTS`.
- **Ship the shortcut** (`▶ Start Comp Agent.lnk`) pointing at the launcher inside
  `_app\`, with a custom icon. This is the "one obvious thing to click."
- **UI shell:** MVP = auto-open the default browser (zero new deps, most robust).
  *Optional later polish:* wrap the same UI in a native chrome-less window via
  `pywebview` so it feels like an app, not a tab — do this only after the browser
  version proves out; it adds a dependency and packaging care.
- Deploy step: copy the `_app\` folder + the shortcut to `X:\28_AI\_AI AGENTS`.
  The real key config lives on the share per §3 (referenced by UNC path); ship only
  a **template** config in the repo, never a real key.
- Add a `--version` and stamp a version string so deployed copies are identifiable.

**Three IT items to clear before rollout (not code — flag to Kyle/IT):**
1. **SmartScreen / Mark-of-the-Web** — an exe run from a network share can trigger
   "Windows protected your PC." IT whitelists the folder/signed exe, or users
   Unblock once. This is the #1 thing that makes a network-launched tool feel
   broken; clear it early.
2. **Antivirus whitelist** — PyInstaller exes are sometimes flagged. Whitelist the
   `X:\28_AI\_AI AGENTS` app path proactively.
3. **Share reachability** — the key config must be readable over the same VPN/share
   access, or the app shows the friendly "check your VPN" message (WS-3).

**Guardrails:** PyInstaller is a build/dev tool, not a runtime dependency of the
library — add it under an optional `[packaging]` extra in `pyproject.toml`, not to
core deps. Don't restructure the package layout to suit the bundler.

**Tests:** add a smoke test that imports the launcher and asserts the server starts
and serves `/` on an ephemeral port (no network/OpenAI needed). Full frozen-binary
testing is manual — document a short QA checklist.

**Risk:** Medium. Data-file bundling and `tkinter`/frozen-path issues are the usual
PyInstaller friction points. Budget time for a manual QA pass on a clean machine
(no Python installed).

---

### WS-3 — Non-technical-user hardening of the local UI

The current UI assumes a developer driving it. **Note what's already done at base
`47e49e8` so you don't rebuild it:** friendly error banners (`_classify_error` →
`friendly_error` → `bannerHtml`) and required-output-folder validation
(`_require_output_root` / `validateOutputRoot`) both already exist. WS-3 is the
*remaining* frictionless polish, built on top of that:

**Do:**
- **First-run / preflight check (NEW):** on startup or first request, verify the
  key config resolved (WS-1) and OpenAI is reachable; if not, route it through the
  *existing* `_classify_error`/banner path with a non-technical message —
  specifically a **"Can't reach the shared key config — check your VPN/network"**
  case for when the share/UNC path is unreachable, and a **"No OpenAI key found —
  contact <admin>"** case when the file is reachable but empty. Add these as new
  classifications, don't fork the mechanism.
- **Default output folder (NEW):** pre-fill the output-folder field with a sensible
  per-user default (e.g. `Documents\Comp Packages`) so a first-time architect isn't
  staring at an empty required field. Keep the existing validation.
- **Persist last-used settings (NEW):** remember output folder + live-search toggle
  in a small per-user local file so architects don't re-enter them each run.
- **Auto-open on launch (ties to WS-2):** the launcher opens the browser to the UI
  so "click shortcut → form appears" needs no manual URL step.

**Guardrails:** the embedded `INDEX_HTML` and JSON API shapes are fine to extend
additively; don't rename existing endpoints or JSON fields (the future frontend/web
service reuses them). Keep it a single-file UI — no build step, no JS framework
(consistent with "no framework rewrites").

**Risk:** Low–Medium. Pure additive UX; the main care is not breaking the existing
API field names.

---

### WS-4 — Product identity & docs de-coupling (low risk, do alongside)

The repo currently frames Comp Agent as "one of several backend agents" behind a
Pelli frontend/orchestrator. That framing is now wrong. **Branding in the deck
stays Pelli — this is about repo/product framing and docs, not visual branding.**

**Do:**
- Update `README.md`, `docs/architecture.md`, `docs/api_contract.md`, and `CLAUDE.md`
  to describe Comp Agent as a **standalone tool with an internal desktop
  distribution now and a hosted web service later** — rather than a bolt-on called
  by an external frontend. Keep the stage-contract docs authoritative; just
  re-frame the surrounding narrative.
- Decide the **product name** later (Kyle open item). Until then, do **not** rename
  the Python package (`comp_agent`), the console script, env-var prefixes, or the
  `comp-agent` distribution name — those are load-bearing across code, tests, and
  the mapped-drive config keys. A product/display name can live in UI copy and docs
  without touching identifiers.

**Guardrails:** docs-only + string copy. No code identifier renames (breaking).

**Risk:** Low.

---

### WS-5 — Preserve the web-service seam (design note, mostly *don't build*)

The web service is deferred, but the plan should keep it cheap to add later:

- The `CompAppStages` API and the file-based artifacts are already the right
  substrate for a hosted mode. **Do not couple new desktop/config/packaging code to
  the local single-user assumptions** in a way that blocks a future multi-tenant
  server (e.g. don't hardcode the mapped-drive path deep in stage logic — keep it in
  the config layer from WS-1).
- The README already sketches the production API (`POST /jobs`,
  `GET /jobs/{id}`, `POST /jobs/{id}/approve`, `GET /jobs/{id}/outputs`). Leave that
  as the documented future shape. **Building it, adding auth, or adding a database is
  explicitly out of scope and needs Kyle's approval per `CLAUDE.md`.**

**Risk:** None (no build). This is a "don't paint into a corner" reminder.

---

## 5. Suggested sequencing

1. **WS-1 (config/secrets)** — foundational; everything else assumes it.
2. **WS-3 (UI hardening)** — can proceed in parallel with WS-1's tail.
3. **WS-2 (packaging)** — after WS-1 lands, since the bundle depends on config
   resolution and the launcher.
4. **WS-4 (docs/identity)** — anytime; low risk; good to land with WS-2 so deployed
   docs match reality.
5. **WS-5** — a review gate, not a task: check the above didn't block the future
   server.

Land each workstream as its own small PR with `pytest` green.

---

## 6. Hard guardrails for the implementer (from CLAUDE.md — still binding)

- **No framework rewrites.** Keep the stdlib `ThreadingHTTPServer`. No FastAPI/Flask.
- **Do not change** `CompAppStages` method names, signatures, or return-dict keys.
- **Do not change** the JSON/CSV filenames or schemas in `docs/stage_contracts.md`.
- **`models.py` dataclasses are the wire format** — additive fields only; no renames
  or removals.
- **The full test suite (51 tests across `tests/`) stays green.** If a change forces
  a contract-assertion edit, stop and ask — that's a signal you're breaking a contract.
- **No new core dependencies** without justification. PyInstaller goes in an optional
  `[packaging]` extra, not core.
- **Branding stays Pelli** this phase. Don't touch the logo, colors, or layout in
  `deck.py` except the freeze-safe path lookup in WS-2.
- **No database, no auth, no server replacement** without Kyle's explicit approval.

---

## 7. Open items for Kyle (decisions, not code)

1. **Deploy home — CONFIRMED:** `X:\28_AI\_AI AGENTS` (`\\datafiles`). Fable 5 uses
   the **UNC path** for the key-config default lookup and deploy docs. Kyle: confirm
   the exact filename/subfolder for the key config (e.g.
   `\\datafiles\28_AI\_AI AGENTS\CompAgent\comp_agent.config`).
2. **Key access — Kyle's call, RELAXED:** all VPN users reading the config folder is
   acceptable; no NTFS restriction required now. The requirement is UX (no key
   entry, no loose local key file), not hard secrecy. Restricting the folder to an
   architect group is a later IT lever if the trust boundary tightens — not a task.
3. **OpenAI usage cap:** set a monthly spend limit on the shared key before rollout.
4. **SmartScreen + antivirus whitelist** for `X:\28_AI\_AI AGENTS` (WS-2 IT items) —
   clear with IT before rollout so first-click isn't blocked.
5. **Product name:** deferred; needed before external marketing, not before internal
   rollout. Until chosen, code identifiers stay `comp_agent`.
6. **Escalation trigger:** decide the condition under which key-on-client becomes
   unacceptable and the LAN proxy gets built. Documented, not built.
