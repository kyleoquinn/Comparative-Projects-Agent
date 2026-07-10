# Comp Agent — Build & Deploy (internal office distribution)

How to build the desktop app and put it on the office share so an architect
can: go to the folder → click one thing → fill the form → pick output → get a
PowerPoint. No Python, no key entry, no server.

## 1. Build

From a machine with Python 3.10+ and this repo checked out:

```
cd "C:/Users/koquinn/Desktop/Comp Agent"
python -m venv .venv-build
.venv-build/Scripts/python -m pip install -e ".[packaging]"
.venv-build/Scripts/python -m PyInstaller packaging/comp_agent.spec --noconfirm
```

That produces a **onedir** bundle at `dist/CompAgent/` containing
`CompAgent.exe` plus its `_internal/` runtime (Python, python-pptx, Pillow,
Tcl/Tk for the folder picker, and the Pelli logo asset). Onedir is deliberate —
do **not** switch to onefile (it unpacks to `%TEMP%` on every launch: slow over
VPN, worse for antivirus).

To keep the repo clean, you can redirect build output:

```
.venv-build/Scripts/python -m PyInstaller packaging/comp_agent.spec --noconfirm --distpath <somewhere>/dist --workpath <somewhere>/build
```

Quick sanity check of the build before deploying:

```
dist/CompAgent/CompAgent.exe --version
dist/CompAgent/CompAgent.exe --no-browser
```

The second command should print `Comp Agent is running at http://127.0.0.1:8765/`
(or a fallback port); open that URL, confirm the "Comp Study Deck Builder" form
renders, then Ctrl+C.

## 2. Deploy layout on the share

Deploy home (confirmed): `X:\28_AI\_AI AGENTS` — always referenced by its UNC
form `\\datafiles\28_AI\_AI AGENTS` in configs and shortcuts, **never** the
`X:` drive letter (drive mappings differ per machine; UNC is the same for
everyone on VPN).

```
\\datafiles\28_AI\_AI AGENTS\
├── ▶ Start Comp Agent.lnk          <- the one thing users click
├── _app\                           <- hidden; the onedir bundle
│   ├── CompAgent.exe
│   └── _internal\...
└── CompAgent\                      <- key config folder (see §3)
    └── comp_agent.config.json
```

Steps:

1. Copy the entire `dist/CompAgent/` folder to the share as
   `\\datafiles\28_AI\_AI AGENTS\_app`.
2. Mark `_app` hidden so users don't wander into it:
   `attrib +h "\\datafiles\28_AI\_AI AGENTS\_app"`
3. Create the shortcut at the top level:
   - Right-click in `X:\28_AI\_AI AGENTS` → New → Shortcut.
   - Target: `\\datafiles\28_AI\_AI AGENTS\_app\CompAgent.exe`
     (UNC target, so the shortcut works regardless of each user's drive letters).
   - Name it `▶ Start Comp Agent`.
   - Leave "Start in" blank or set it to the user's home; the app does not
     depend on its working directory (users pick the output folder in the UI).
   - Properties → Change Icon… → browse to an `.ico` of your choice (a Pelli
     icon works; any `.ico` on the share is fine — pick one that stands out).

   Or script it (PowerShell, run once by whoever deploys):

   ```powershell
   $ws = New-Object -ComObject WScript.Shell
   $lnk = $ws.CreateShortcut('\\datafiles\28_AI\_AI AGENTS\▶ Start Comp Agent.lnk')
   $lnk.TargetPath = '\\datafiles\28_AI\_AI AGENTS\_app\CompAgent.exe'
   $lnk.Description = 'Start the Comp Agent deck builder'
   # Optional custom icon:
   # $lnk.IconLocation = '\\datafiles\28_AI\_AI AGENTS\_app\comp_agent.ico,0'
   $lnk.Save()
   ```

Note: even though the bytes live on the share, this is **not** a shared
server. The exe runs in each user's own Windows session and binds
`127.0.0.1` on *their* PC; multiple architects can run it at once. The key
config is the only truly shared file.

## 3. Key config (the shared OpenAI key)

The key is **never** baked into the exe. The app resolves config at startup in
layered order (process env → `COMP_AGENT_CONFIG` → app-adjacent file → shared
UNC default → repo `.env`); for office deploys, use the shared default:

- Path: `\\datafiles\28_AI\_AI AGENTS\CompAgent\comp_agent.config.json`
  (a `comp_agent.env` KEY=VALUE file also works; JSON is checked first).
- Contents (Notepad-editable):

  ```json
  {
    "OPENAI_API_KEY": "sk-...",
    "COMP_AGENT_LIVE_SEARCH": "1"
  }
  ```

- Other supported keys: `COMP_AGENT_OPENAI_MODEL`, `COMP_AGENT_OPENAI_TIMEOUT`,
  `COMP_AGENT_OPENAI_RETRIES`, `COMP_AGENT_RESEARCH_CONCURRENCY`,
  `COMP_AGENT_FIELD_REPAIR_LIMIT`. See `.env.example` in the repo.
- **Key rotation:** edit that one file on the share; every machine picks up the
  new key on its next launch. No rebuild, no redeploy.
- Set a **monthly usage cap** on the key in the OpenAI dashboard before rollout.
- Never commit a real key to the repo; only `.env.example` templates ship.

## 4. Updating the app

1. Build a fresh `dist/CompAgent/` (bump `version` in `pyproject.toml` first so
   `--version` identifies deployed copies).
2. Ask users to close any running Comp Agent windows (Windows locks in-use
   exes on shares).
3. Replace the folder: rename the old one to `_app.old`, copy the new bundle in
   as `_app` (rename-then-copy beats copy-over-in-place — no half-updated
   state), re-apply `attrib +h`, then delete `_app.old` once confirmed.
4. The shortcut keeps working — it points at the folder path, not a version.
5. Everyone gets the new version on their next click; no per-machine install.

## 5. Manual QA checklist (before each rollout)

Run on a **clean machine — no Python installed** — logged in as a normal
(non-admin) user on VPN:

- [ ] `X:\28_AI\_AI AGENTS` reachable; double-click `▶ Start Comp Agent`.
- [ ] No SmartScreen block (or the documented one-time Unblock works — see §6).
- [ ] Console window appears; version line prints; "OpenAI key: found."
- [ ] Browser opens automatically to `http://127.0.0.1:<port>/` and the
      "Comp Study Deck Builder" form renders.
- [ ] Launch a **second** copy while the first is running → it falls back to
      another port and both work.
- [ ] "Browse…" for the output folder opens the native picker (tkinter is in
      the bundle); typing a path manually also works.
- [ ] Run a discovery end-to-end; approve; confirm a `.pptx` lands in the
      chosen output folder and **the Pelli logo renders on the slides**
      (verifies the frozen asset lookup).
- [ ] Disconnect VPN, relaunch: app still starts, shows the no-key/check-VPN
      messaging instead of hanging (share probe must time out in ~2.5s).
- [ ] Ctrl+C (or close the console window) shuts everything down; no stray
      `CompAgent.exe` in Task Manager.

## 6. IT items to clear before rollout (not code)

1. **SmartScreen / Mark-of-the-Web** — an exe launched from a network share can
   trigger "Windows protected your PC." Options: IT whitelists the
   `\\datafiles\28_AI\_AI AGENTS` path (Intune/GPO), signs the exe, or each
   user does a one-time Unblock (file Properties → Unblock, or "More info →
   Run anyway"). This is the #1 thing that makes a network-launched tool feel
   broken — clear it early.
2. **Antivirus whitelist** — PyInstaller bundles are sometimes flagged by AV
   heuristics. Proactively whitelist the `\\datafiles\28_AI\_AI AGENTS\_app`
   folder (and the top-level shortcut path) with whatever endpoint protection
   the office runs.
3. **VPN / share reachability** — the key config lives on the share, so users
   must have the same VPN/share access to get live search. If the share is
   unreachable the app still starts (placeholder data) and tells the user to
   check their VPN; confirm with IT that all intended users can read
   `\\datafiles\28_AI\_AI AGENTS\CompAgent\`.
