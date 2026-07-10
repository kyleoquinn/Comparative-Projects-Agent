from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from comp_agent.config import resolve_config
from comp_agent.models import ProjectBrief
from comp_agent.stages import CompAppStages
from comp_agent.workspace import slugify, to_jsonable, write_csv, write_json


DEFAULT_OUTPUT_ROOT = "projects_ui"
JobUpdate = Callable[[str, int], None]
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


def run_server(host: str = "127.0.0.1", port: int = 8765, output_root: str = DEFAULT_OUTPUT_ROOT) -> None:
    handler = _handler_for(output_root)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Comp Agent UI running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


def _handler_for(default_output_root: str):
    class CompAgentHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path in {"/", "/index.html"}:
                self._send_html(INDEX_HTML)
                return
            if self.path == "/api/preflight":
                self._send_json(_preflight_report())
                return
            if self.path == "/api/settings":
                self._send_json({"settings": _load_settings(), "path": str(_settings_path())})
                return
            if self.path.startswith("/api/jobs/"):
                self._handle_job_status()
                return
            self.send_error(404)

        def do_POST(self) -> None:
            if self.path == "/api/discover/start":
                self._start_job("discover", default_output_root)
                return
            if self.path == "/api/approve/start":
                self._start_job("approve", default_output_root)
                return
            if self.path == "/api/select-output-folder":
                self._handle_select_output_folder()
                return
            if self.path == "/api/settings":
                self._handle_save_settings()
                return
            if self.path == "/api/discover":
                self._handle_discover(default_output_root)
                return
            if self.path == "/api/approve":
                self._handle_approve(default_output_root)
                return
            self.send_error(404)

        def _handle_discover(self, default_output_root: str) -> None:
            payload = self._read_json()
            try:
                self._send_json(_run_discover(payload, default_output_root))
            except Exception as error:
                # Validation errors (e.g. missing output folder) must come
                # back as JSON, not a dropped connection.
                self._send_json(self._error_payload(error), status=400)

        def _handle_approve(self, default_output_root: str) -> None:
            payload = self._read_json()
            try:
                self._send_json(_run_approve(payload, default_output_root))
            except Exception as error:
                self._send_json(self._error_payload(error), status=400)

        @staticmethod
        def _error_payload(error: Exception) -> dict[str, Any]:
            return {
                "error": str(error),
                "friendly_error": _classify_error(f"{type(error).__name__}: {error}"),
            }

        def _start_job(self, kind: str, default_output_root: str) -> None:
            payload = self._read_json()
            job_id = _create_job(kind)
            worker = threading.Thread(
                target=_run_job,
                args=(job_id, kind, payload, default_output_root),
                daemon=True,
            )
            worker.start()
            self._send_json({"job_id": job_id, "status_url": f"/api/jobs/{job_id}"}, status=202)

        def _handle_job_status(self) -> None:
            job_id = self.path.rsplit("/", 1)[-1]
            job = _get_job(job_id)
            if not job:
                self._send_json({"error": "Job not found."}, status=404)
                return
            self._send_json(job)

        def _handle_select_output_folder(self) -> None:
            self._send_json({"path": _select_output_folder()})

        def _handle_save_settings(self) -> None:
            payload = self._read_json()
            try:
                saved = _save_settings(payload)
            except Exception:
                # Settings persistence is best-effort; a broken profile dir
                # must never fail the request (or the run that triggered it).
                self._send_json(
                    {"ok": False, "settings": _sanitize_settings(payload), "path": str(_settings_path())}
                )
                return
            self._send_json({"ok": True, "settings": saved, "path": str(_settings_path())})

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return CompAgentHandler


def _brief_from_payload(payload: dict[str, Any]) -> ProjectBrief:
    filters = dict(payload.get("filters") or {})
    max_comps = payload.get("max_comps")
    if max_comps not in (None, ""):
        filters["max_comps"] = int(max_comps)
    normalized = {
        **payload,
        "project_name": payload.get("project_name") or "Comp Search Test",
        "address": payload.get("address") or "",
        "program_type": payload.get("program_type") or "office repositioning",
        "geography": payload.get("geography") or "New York, NY",
        "radius_miles": payload.get("radius_miles") or 3,
        "time_horizon_years": payload.get("time_horizon_years") or 8,
        "audience": payload.get("audience") or "client presentation",
        "filters": filters,
        "presentation_priorities": payload.get("presentation_priorities")
        or [
            "source-backed comp selection",
            "contract data extraction",
            "presentation-ready precedent story",
        ],
    }
    return ProjectBrief.from_dict(normalized)


def _require_output_root(payload: dict[str, Any]) -> str:
    raw = str(payload.get("output_root") or "").strip()
    if not raw:
        raise ValueError("Output folder is required. Pick a destination folder before running.")
    if not Path(raw).is_absolute():
        raise ValueError(f"Output folder must be an absolute path. Got: {raw}")
    return raw


def _run_discover(payload: dict[str, Any], default_output_root: str, update: JobUpdate | None = None) -> dict[str, Any]:
    def progress(message: str, percent: int) -> None:
        if update:
            update(message, percent)

    output_root = _require_output_root(payload)
    progress("Preparing project brief", 5)
    brief = _brief_from_payload(payload)
    must_include_comps = _must_include_comps_for_discovery(brief, str(payload.get("user_defined_comps") or ""))
    if must_include_comps:
        brief.filters["excluded_user_defined_comps"] = [
            {
                "name": item["name"],
                "location": item["location"],
                "note": item["note"],
            }
            for item in must_include_comps
        ]

    previous_live_search = os.environ.get("COMP_AGENT_LIVE_SEARCH")
    if payload.get("live_search", True):
        os.environ["COMP_AGENT_LIVE_SEARCH"] = "1"
        progress("Connecting to OpenAI live web search", 15)
    else:
        os.environ.pop("COMP_AGENT_LIVE_SEARCH", None)
        progress("Preparing local candidate strategy", 15)
    try:
        stages = CompAppStages(output_root=output_root)
        progress("Searching comparable projects", 30)
        paths = stages.discover(brief)
    finally:
        if previous_live_search is None:
            os.environ.pop("COMP_AGENT_LIVE_SEARCH", None)
        else:
            os.environ["COMP_AGENT_LIVE_SEARCH"] = previous_live_search

    progress("Preparing approval list", 82)
    candidates = _read_json_file(paths["candidate_comps"])
    candidates = _merge_user_defined_candidates(brief, candidates, must_include_comps)
    if candidates:
        progress("Saving candidate comps", 88)
        paths["candidate_comps"] = write_json(paths["candidate_comps"], candidates)
        paths["candidate_comps_csv"] = _write_candidate_csv(paths["candidate_comps_csv"], candidates)
    progress("Capturing source log", 94)
    source_log = _read_json_file(paths["source_log"])
    live_search_status = _live_search_status_from_log(source_log)
    progress("Candidate search complete", 100)
    return {
        "brief": to_jsonable(brief),
        "output_root": output_root,
        "paths": {key: str(value) for key, value in paths.items()},
        "candidates": candidates,
        "source_log": source_log,
        "live_search_status": live_search_status,
    }


def _run_approve(payload: dict[str, Any], default_output_root: str, update: JobUpdate | None = None) -> dict[str, Any]:
    def progress(message: str, percent: int) -> None:
        if update:
            update(message, percent)

    output_root = _require_output_root(payload)
    progress("Reading approved comp selection", 5)
    brief = _brief_from_payload(payload.get("brief") or payload)
    raw_ids = payload.get("comp_ids") or payload.get("approved_ids") or []
    comp_ids = [str(value) for value in raw_ids if value]

    paths: dict[str, Path] = {}
    previous_live_search = os.environ.get("COMP_AGENT_LIVE_SEARCH")
    if payload.get("live_search", True):
        os.environ["COMP_AGENT_LIVE_SEARCH"] = "1"
    else:
        os.environ.pop("COMP_AGENT_LIVE_SEARCH", None)
    try:
        stages = CompAppStages(output_root=output_root)
        steps = [
            ("Saving approvals", 12, lambda: stages.approve(brief, approved_ids=comp_ids, limit=None, notes="Approved from temporary UI.")),
            ("Enriching approved comps", 24, lambda: stages.research(brief)),
            ("Repairing incomplete comp packages", 40, lambda: stages.format_outputs(brief)),
            ("Validating image packages", 56, lambda: stages.generate_outputs(brief)),
            ("Auditing deck completeness", 72, lambda: {}),
            ("Running targeted field repairs", 80, lambda: {}),
            ("Writing Comp Study Deck", 84, lambda: {}),
            ("Running diligence checks", 88, lambda: stages.audit(brief)),
        ]
        for message, percent, step in steps:
            progress(message, percent)
            paths.update(step())
    finally:
        if previous_live_search is None:
            os.environ.pop("COMP_AGENT_LIVE_SEARCH", None)
        else:
            os.environ["COMP_AGENT_LIVE_SEARCH"] = previous_live_search
    strategy = _read_json_file(paths["deck_strategy"]) if "deck_strategy" in paths else {}
    progress("Deck package complete", 100)
    return {"paths": {key: str(value) for key, value in paths.items()}, "deck_strategy": strategy}


def _create_job(kind: str) -> str:
    job_id = uuid.uuid4().hex
    now = time.time()
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "job_id": job_id,
            "kind": kind,
            "status": "queued",
            "message": "Queued",
            "percent": 0,
            "created_at": now,
            "updated_at": now,
            "elapsed_seconds": 0,
            "result": None,
            "error": "",
        }
    return job_id


def _run_job(job_id: str, kind: str, payload: dict[str, Any], default_output_root: str) -> None:
    try:
        _update_job(job_id, "Starting", 1, status="running")
        if kind == "discover":
            result = _run_discover(payload, default_output_root, lambda message, percent: _update_job(job_id, message, percent))
        elif kind == "approve":
            result = _run_approve(payload, default_output_root, lambda message, percent: _update_job(job_id, message, percent))
        else:
            raise ValueError(f"Unsupported job type: {kind}")
        _complete_job(job_id, result)
    except Exception as error:
        _fail_job(job_id, error)


def _update_job(job_id: str, message: str, percent: int, *, status: str = "running") -> None:
    now = time.time()
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job.update(
            {
                "status": status,
                "message": message,
                "percent": max(0, min(100, int(percent))),
                "updated_at": now,
                "elapsed_seconds": round(now - float(job["created_at"]), 1),
            }
        )


def _complete_job(job_id: str, result: dict[str, Any]) -> None:
    now = time.time()
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job.update(
            {
                "status": "complete",
                "message": "Complete",
                "percent": 100,
                "updated_at": now,
                "elapsed_seconds": round(now - float(job["created_at"]), 1),
                "result": result,
            }
        )


def _fail_job(job_id: str, error: Exception) -> None:
    now = time.time()
    raw_error = f"{error}\n{traceback.format_exc()}"
    friendly = _classify_error(f"{type(error).__name__}: {error}\n{raw_error}")
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job.update(
            {
                "status": "failed",
                "message": friendly["headline"],
                "updated_at": now,
                "elapsed_seconds": round(now - float(job["created_at"]), 1),
                "error": raw_error,
                "friendly_error": friendly,
            }
        )


def _get_job(job_id: str) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return None
        snapshot = dict(job)
    snapshot["elapsed_seconds"] = round(time.time() - float(snapshot["created_at"]), 1)
    return snapshot


def _select_output_folder() -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="Select Comp Agent output folder")
        root.destroy()
        return selected or ""
    except Exception:
        return ""


def _default_output_root() -> str:
    """Per-user default output folder shown when the field would be empty.

    Computed only — never created here. Existing stage code creates output
    directories when a run actually starts.
    """
    return str(Path.home() / "Documents" / "Comp Packages")


def _settings_path() -> Path:
    """Per-user settings file: %LOCALAPPDATA%\\CompAgent\\settings.json.

    Falls back to the home directory when LOCALAPPDATA is unset (non-Windows
    test environments).
    """
    base = (os.environ.get("LOCALAPPDATA") or "").strip()
    root = Path(base) if base else Path.home()
    return root / "CompAgent" / "settings.json"


def _sanitize_settings(payload: Any) -> dict[str, Any]:
    """Keep only known, correctly-typed settings keys. Never raises."""
    settings: dict[str, Any] = {}
    if not isinstance(payload, dict):
        return settings
    output_root = payload.get("output_root")
    if isinstance(output_root, str) and output_root.strip():
        settings["output_root"] = output_root.strip()
    live_search = payload.get("live_search")
    if isinstance(live_search, bool):
        settings["live_search"] = live_search
    return settings


def _load_settings() -> dict[str, Any]:
    """Read saved per-user settings; corrupt or missing files yield {}."""
    try:
        raw = _settings_path().read_text(encoding="utf-8")
        return _sanitize_settings(json.loads(raw))
    except Exception:
        return {}


_SETTINGS_LOCK = threading.Lock()


def _save_settings(payload: Any) -> dict[str, Any]:
    """Merge sanitized settings over existing ones and persist to disk.

    Serialized under a lock (the server is threaded) and written via a temp
    file + atomic ``os.replace`` so a mid-write kill or concurrent saves can
    never leave a truncated settings.json behind.
    """
    with _SETTINGS_LOCK:
        settings = {**_load_settings(), **_sanitize_settings(payload)}
        path = _settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    return settings


def _key_source_from_report(report: dict[str, Any]) -> str | None:
    """Best-effort name of the config layer that supplies OPENAI_API_KEY.

    Prefers a layer that set the key during this resolution, then a loaded
    layer whose file contains the key (the usual case when startup already
    resolved config before the preflight ran), then plain process env. The
    env-vs-file distinction is heuristic by design — key VALUES are never
    inspected or compared.
    """
    layers = report.get("layers") or []
    for entry in layers:
        if "OPENAI_API_KEY" in (entry.get("keys_set") or []):
            return str(entry.get("layer") or "") or None
    for entry in layers:
        if "OPENAI_API_KEY" in (entry.get("keys_already_in_env") or []):
            return str(entry.get("layer") or "") or None
    if report.get("openai_key_present"):
        return "env"
    return None


def _openai_reachable(timeout: float = 3.0) -> bool:
    """Best-effort check that this machine can reach the OpenAI API at all.

    Sends an UNAUTHENTICATED request — the key never leaves the process. Any
    HTTP response (including the expected 401) proves reachability; only a
    transport-level failure (DNS, firewall, proxy block) counts as
    unreachable. Uses the default opener so it exercises the same
    proxy-honoring network path the real job-time OpenAI calls take.
    """
    import urllib.error
    import urllib.request

    try:
        request = urllib.request.Request("https://api.openai.com/v1/models", method="GET")
        with urllib.request.urlopen(request, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True  # the API answered (401 without auth) — network path works
    except Exception:
        return False


def _preflight_report() -> dict[str, Any]:
    """Secrets-free startup health check for the UI.

    Re-runs layered config resolution in READ-ONLY mode (``apply=False``) so
    a page load can never mutate the process env of an in-flight job (e.g.
    re-enabling COMP_AGENT_LIVE_SEARCH after a run disabled it), then maps
    the common first-run failures to the existing friendly-error copy. Key
    VALUES never appear in this payload — the WS-1 report carries key names
    and source paths only.
    """
    report = resolve_config(apply=False)
    key_present = bool(report.get("openai_key_present"))
    share_reachable = report.get("share_reachable")
    openai_reachable: bool | None = _openai_reachable() if key_present else None
    friendly: dict[str, str] | None = None
    if not key_present:
        if share_reachable is False:
            friendly = _classify_error(
                "Shared key config unreachable: the network share holding the OpenAI key did not respond."
            )
        else:
            friendly = _classify_error(
                "No OpenAI key found in any config location (environment, config files, shared drive, .env)."
            )
    elif openai_reachable is False:
        friendly = _classify_error(
            "OpenAI unreachable: could not reach api.openai.com from this machine."
        )
    return {
        "ok": key_present and openai_reachable is not False,
        "openai_key_present": key_present,
        "openai_reachable": openai_reachable,
        "key_source": _key_source_from_report(report),
        "share_reachable": share_reachable,
        "default_output_root": _default_output_root(),
        "layers": report.get("layers") or [],
        "friendly_error": friendly,
    }


def _must_include_comps_for_discovery(brief: ProjectBrief, raw_text: str = "") -> list[dict[str, str]]:
    items = list(brief.must_include_comps)
    items.extend(_parse_user_defined_comps(raw_text))
    seen: set[str] = set()
    unique = []
    for item in items:
        key = _canonical_candidate_text(item.get("name", ""), item.get("location", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append({"name": item.get("name", ""), "location": item.get("location", ""), "note": item.get("note", "")})
    return unique


def _merge_user_defined_candidates(brief: ProjectBrief, candidates: list[dict[str, Any]] | None, must_include_comps: list[dict[str, str]]) -> list[dict[str, Any]]:
    merged = list(candidates or [])
    for index, item in enumerate(must_include_comps, start=1):
        if not _canonical_candidate_text(item["name"], item["location"]):
            continue
        duplicate = _find_duplicate_candidate(merged, item)
        if duplicate:
            _merge_user_defined_into_candidate(duplicate, item, brief)
            continue
        comp_id = slugify(f"{brief.project_name}-user-comp-{item['name']}")
        note = item["note"] or f"User-added comparable project for {brief.program_type}."
        merged.append(
            {
                "comp_id": comp_id,
                "comp_name": item["name"],
                "location": item["location"] or brief.geography,
                "comp_type": "user_defined",
                "relevance_score": max(50, 92 - index),
                "status": "user_added",
                "candidate_source": "user_defined",
                "user_note": item["note"],
                "known_attributes": {
                    "program_type": brief.program_type,
                    "presentation_takeaway": note,
                    "candidate_source": "user_defined",
                    "_sources": [],
                },
                "missing_attributes": [
                    "public source verification",
                    "scale",
                    "status_year",
                    "owner_developer",
                    "architect_designer",
                    "hero_image",
                ],
                "source_notes": [note, "User-added comp; enrich with live or manual sources before client reliance."],
            }
        )
    return merged


def _find_duplicate_candidate(candidates: list[dict[str, Any]], item: dict[str, str]) -> dict[str, Any] | None:
    for candidate in candidates:
        if _is_duplicate_candidate(candidate, item):
            return candidate
    return None


def _is_duplicate_candidate(candidate: dict[str, Any], item: dict[str, str]) -> bool:
    candidate_name = str(candidate.get("comp_name") or candidate.get("project_name") or "")
    candidate_location = str(candidate.get("location") or "")
    user_name = item["name"]
    user_location = item["location"]
    candidate_key = _canonical_candidate_text(candidate_name, candidate_location)
    user_key = _canonical_candidate_text(user_name, user_location)
    if not candidate_key or not user_key:
        return False
    if candidate_key == user_key:
        return True

    candidate_tokens = set(candidate_key.split("-"))
    user_tokens = set(user_key.split("-"))
    shared = candidate_tokens & user_tokens
    if not shared:
        return False
    candidate_number = _leading_number(candidate_tokens)
    user_number = _leading_number(user_tokens)
    if candidate_number and user_number and candidate_number == user_number:
        return _token_overlap(candidate_tokens, user_tokens) >= 0.5
    return _token_overlap(candidate_tokens, user_tokens) >= 0.78


def _merge_user_defined_into_candidate(candidate: dict[str, Any], item: dict[str, str], brief: ProjectBrief) -> None:
    note = item["note"] or f"User-added comparable project for {brief.program_type}."
    current_source = str(candidate.get("candidate_source") or "live_search")
    candidate["candidate_source"] = "user_defined_and_live" if current_source != "user_defined" else "user_defined"
    candidate["user_note"] = note
    if not candidate.get("location") and item["location"]:
        candidate["location"] = item["location"]
    attrs = candidate.get("known_attributes") if isinstance(candidate.get("known_attributes"), dict) else {}
    attrs["candidate_source"] = candidate["candidate_source"]
    if note and not attrs.get("user_note"):
        attrs["user_note"] = note
    candidate["known_attributes"] = attrs
    source_notes = list(candidate.get("source_notes") or [])
    merge_note = f"User-defined duplicate merged: {item['name']}"
    if item["location"]:
        merge_note += f" | {item['location']}"
    if note:
        merge_note += f" | {note}"
    if merge_note not in source_notes:
        source_notes.append(merge_note)
    candidate["source_notes"] = source_notes


def _parse_user_defined_comps(raw_text: str) -> list[dict[str, str]]:
    comps: list[dict[str, str]] = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("|")]
        name = parts[0] if parts else ""
        if not name:
            continue
        comps.append(
            {
                "name": name,
                "location": parts[1] if len(parts) > 1 else "",
                "note": parts[2] if len(parts) > 2 else "",
            }
        )
    return comps


def _canonical_candidate_text(name: str, location: str = "") -> str:
    text = f"{name} {location}".lower()
    replacements = {
        "&": " and ",
        "avenue": " ave ",
        "avenu": " ave ",
        "ave.": " ave ",
        "av ": " ave ",
        "street": " st ",
        "st.": " st ",
        "road": " rd ",
        "rd.": " rd ",
        "boulevard": " blvd ",
        "blvd.": " blvd ",
        "place": " pl ",
        "pl.": " pl ",
        "new york city": " nyc ",
        "new york": " ny ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    ordinal_words = {
        "first": "1",
        "second": "2",
        "third": "3",
        "fourth": "4",
        "fifth": "5",
        "sixth": "6",
        "seventh": "7",
        "eighth": "8",
        "ninth": "9",
        "tenth": "10",
        "eleventh": "11",
        "twelfth": "12",
    }
    for word, number in ordinal_words.items():
        text = text.replace(word, number)
    text = re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1", text)
    stopwords = {"the", "building", "tower", "ny", "nyc", "usa", "united", "states"}
    tokens = [token for token in slugify(text).split("-") if token and token not in stopwords]
    return "-".join(tokens)


def _leading_number(tokens: set[str]) -> str:
    numbers = sorted(token for token in tokens if token.isdigit())
    return numbers[0] if numbers else ""


def _token_overlap(left: set[str], right: set[str]) -> float:
    denominator = min(len(left), len(right))
    if denominator == 0:
        return 0.0
    return len(left & right) / denominator


def _write_candidate_csv(path: str | Path, candidates: list[dict[str, Any]]) -> Path:
    rows = []
    for candidate in candidates:
        attrs = candidate.get("known_attributes") or {}
        rows.append(
            {
                **candidate,
                "known_attributes": "; ".join(f"{key}: {value}" for key, value in attrs.items()),
                "missing_attributes": "; ".join(candidate.get("missing_attributes") or []),
                "source_notes": "; ".join(candidate.get("source_notes") or []),
            }
        )
    return write_csv(
        path,
        rows,
        [
            "comp_id",
            "comp_name",
            "location",
            "comp_type",
            "relevance_score",
            "status",
            "known_attributes",
            "missing_attributes",
            "source_notes",
        ],
    )


def _read_json_file(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _classify_error(text: str) -> dict[str, str]:
    """Map any error/warning string to a user-friendly headline + detail.

    Used for both fatal job failures and non-fatal live-search warnings so
    the UI can show a manager-readable message instead of raw OpenAI JSON or
    Python tracebacks.
    """
    t = (text or "").lower()

    # Shared key config / preflight. Checked first so share and key-lookup
    # problems get the office-specific copy instead of the generic
    # network/timeout messages below. The same classifications cover
    # job-time failures of the same nature.
    if "shared key config" in t:
        return {
            "headline": "Can't Reach the Shared Key Config",
            "detail": "Check your VPN/network connection. The OpenAI key lives on the office share and could not be read; reconnect and try again.",
        }
    if (
        "no openai key found" in t
        or "openai_api_key is not set" in t
        or ("api key" in t and "not set" in t)
    ):
        return {
            "headline": "No OpenAI Key Found",
            "detail": "The app couldn't find an OpenAI key in any config location, so live search can't run. Contact your Comp Agent admin.",
        }
    if "openai unreachable" in t or ("api.openai.com" in t and ("could not reach" in t or "unreachable" in t or "blocked" in t)):
        return {
            "headline": "Can't Reach OpenAI",
            "detail": "This machine couldn't reach api.openai.com — the office network, a firewall, or a proxy may be blocking it. Live search won't work until it's reachable. Contact IT if this persists.",
        }

    # Output folder validation (raised before any pipeline work starts).
    if "output folder" in t:
        return {
            "headline": "Output Folder Problem",
            "detail": "The output folder must be a full absolute path (for example C:\\Users\\you\\Documents\\Comp Packages). Pick the folder again with Browse and re-run.",
        }

    # OpenAI / live search
    if "insufficient_quota" in t or "exceeded your current quota" in t:
        return {
            "headline": "Live Search Failed: Out of API Credits",
            "detail": "The OpenAI account has no remaining credit. Add credits in the OpenAI billing portal and try again.",
        }
    if "http 429" in t or "rate limit" in t or "rate-limit" in t:
        return {
            "headline": "Live Search Failed: Rate Limit Hit",
            "detail": "Too many requests to OpenAI in a short window. Wait a minute and try again.",
        }
    if "http 401" in t or "invalid_api_key" in t or "incorrect api key" in t:
        return {
            "headline": "Live Search Failed: API Key Rejected",
            "detail": "The OpenAI key was rejected as invalid or revoked. Contact your Comp Agent admin to update the shared key.",
        }
    if "http 403" in t:
        return {
            "headline": "Live Search Failed: Access Denied",
            "detail": "The OpenAI account does not have access to the requested model or feature.",
        }
    if "http 5" in t or "service unavailable" in t or "bad gateway" in t or "gateway timeout" in t:
        return {
            "headline": "Live Search Failed: OpenAI Service Unavailable",
            "detail": "OpenAI is having trouble responding. Try again in a few minutes.",
        }
    if "timed out" in t or "timeout" in t:
        return {
            "headline": "Live Search Failed: Timed Out",
            "detail": "Live search took too long to respond. Try fewer comps or simpler comp guidance, or run again.",
        }
    # Network
    if (
        "connectionerror" in t
        or "connection refused" in t
        or "connection reset" in t
        or "name or service not known" in t
        or "name resolution" in t
        or "nodename nor servname" in t
        or "getaddrinfo failed" in t
    ):
        return {
            "headline": "Network Error",
            "detail": "Could not reach the network. Check your internet connection and try again.",
        }

    # Filesystem
    if "permissionerror" in t or "permission denied" in t:
        return {
            "headline": "File Permission Denied",
            "detail": "The agent could not read or write a file. Check permissions on the output folder.",
        }
    if "filenotfounderror" in t or "no such file" in t:
        return {
            "headline": "File Not Found",
            "detail": "An expected input or template file is missing. The output folder may be misconfigured.",
        }
    if "no space left" in t or "disk full" in t:
        return {
            "headline": "Disk Full",
            "detail": "The output drive is out of space. Free up space and try again.",
        }

    # Image / asset
    if "image" in t and ("download" in t or "fetch" in t or "404" in t or "could not retrieve" in t):
        return {
            "headline": "Image Download Failed",
            "detail": "One or more comp images could not be retrieved. The deck may have placeholder slots; re-run to retry.",
        }

    # Parse
    if "unparseable" in t or "could not parse" in t or "jsondecodeerror" in t or ("json" in t and "decode" in t):
        return {
            "headline": "Live Search Returned Bad Data",
            "detail": "OpenAI returned a response that was not valid JSON. Try again or simplify the brief.",
        }

    # Default
    snippet = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return {
        "headline": "Something Went Wrong",
        "detail": snippet[:240] or "An unexpected error occurred. Check the source log for details.",
    }


def _live_search_status_from_log(source_log: list[dict[str, Any]] | None) -> dict[str, str] | None:
    """Read the source log for failed live-search entries and classify them."""
    failed_notes: list[str] = []
    for entry in source_log or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("related_output") or "") != "openai_live_search":
            continue
        if str(entry.get("status") or "").lower() != "failed":
            continue
        note = str(entry.get("notes") or "").strip()
        if note:
            failed_notes.append(note)
    if not failed_notes:
        return None
    return _classify_error("\n".join(failed_notes))


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Comp Study Deck Builder</title>
  <style>
    :root {
      --ink: #242424;
      --muted: #66706c;
      --line: #d8ddd8;
      --paper: #fbfaf7;
      --field: #ffffff;
      --accent: #2f6f64;
      --accent-2: #7b5f33;
      --warn: #a55326;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Aptos, Segoe UI, Arial, sans-serif;
      color: var(--ink);
      background: var(--paper);
    }
    header {
      padding: 22px 28px 16px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
    }
    h1 { margin: 0; font-size: 22px; font-weight: 700; letter-spacing: 0; }
    main {
      display: grid;
      grid-template-columns: minmax(320px, 440px) 1fr;
      min-height: calc(100vh - 70px);
    }
    aside {
      padding: 22px 24px;
      border-right: 1px solid var(--line);
      background: #f4f2ec;
    }
    section { padding: 22px 28px; }
    label { display: block; margin: 14px 0 6px; font-size: 13px; color: var(--muted); }
    input, textarea, select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--field);
      padding: 10px 11px;
      font: inherit;
      color: var(--ink);
    }
    input::placeholder, textarea::placeholder { color: #9ba39e; opacity: 1; }
    textarea { min-height: 74px; resize: vertical; }
    .must-include-comp-input, .comp-type-input, .design-priority-input {
      margin-bottom: 8px;
    }
    .must-include-comp-input:last-child, .comp-type-input:last-child, .design-priority-input:last-child {
      margin-bottom: 0;
    }
    .form-group {
      margin-top: 22px;
      padding-top: 18px;
      border-top: 1px solid var(--line);
    }
    .form-group h2 {
      margin: 0 0 4px;
      font-size: 15px;
      letter-spacing: 0;
    }
    .hint {
      margin: 0 0 10px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .path-row { display: grid; grid-template-columns: 1fr auto; gap: 8px; align-items: center; }
    .path-row button { padding-left: 12px; padding-right: 12px; white-space: nowrap; }
    .checks {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-top: 8px;
    }
    .check {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      color: var(--ink);
    }
    .check input { width: auto; }
    button {
      border: 0;
      border-radius: 6px;
      padding: 10px 14px;
      background: var(--accent);
      color: #ffffff;
      font-weight: 700;
      cursor: pointer;
    }
    button.secondary { background: #343a37; }
    button:disabled { opacity: .55; cursor: wait; }
    .actions { display: flex; gap: 10px; margin-top: 18px; align-items: center; }
    .status { color: var(--muted); font-size: 13px; }
    .progress {
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      padding: 14px 0;
      margin-bottom: 18px;
    }
    .progress-top {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
    }
    .progress-bar {
      width: 100%;
      height: 7px;
      overflow: hidden;
      border-radius: 999px;
      background: #e1ded5;
    }
    .progress-fill {
      width: 0%;
      height: 100%;
      background: var(--accent);
      transition: width .25s ease;
    }
    .progress-message {
      margin-top: 8px;
      font-size: 14px;
      color: var(--ink);
    }
    .loading {
      display: inline-block;
      width: 16px;
      height: 16px;
      border: 2px solid #f3f3f3;
      border-top: 2px solid var(--accent);
      border-radius: 50%;
      animation: spin 1s linear infinite;
      margin-right: 8px;
    }
    @keyframes spin {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }
    .button-loading {
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .candidate {
      border-top: 1px solid var(--line);
      padding: 16px 0;
      display: grid;
      grid-template-columns: 28px 1fr;
      gap: 12px;
    }
    .candidate h3 { margin: 0 0 5px; font-size: 17px; }
    .meta { color: var(--muted); font-size: 13px; margin-bottom: 8px; }
    .takeaway { font-size: 14px; line-height: 1.42; max-width: 900px; }
    .sources {
      margin-top: 18px;
      padding-top: 18px;
      border-top: 1px solid var(--line);
    }
    .source { font-size: 13px; margin: 8px 0; color: var(--muted); }
    .output {
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      padding: 14px 0;
      margin: 0 0 18px;
    }
    .output h2 { margin-top: 0; }
    .output-grid {
      display: grid;
      grid-template-columns: 170px 1fr;
      gap: 8px 14px;
      font-size: 13px;
    }
    .output-grid div:nth-child(odd) { color: var(--muted); }
    .empty {
      color: var(--muted);
      padding: 60px 0;
      max-width: 620px;
      line-height: 1.5;
    }
    code { background: #ece8df; padding: 2px 5px; border-radius: 4px; }
    .banner {
      border-radius: 8px;
      padding: 14px 16px;
      margin: 0 0 18px;
      border-left: 4px solid var(--warn);
      background: #fdf3ea;
    }
    .banner h3 { margin: 0 0 4px; font-size: 15px; color: var(--warn); }
    .banner p { margin: 0; font-size: 13px; line-height: 1.45; color: var(--ink); }
    .scope-grid {
      display: flex;
      flex-direction: column;
      gap: 8px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--field);
    }
    .scope-row {
      display: grid;
      grid-template-columns: 130px 90px 1fr;
      align-items: center;
      gap: 10px;
    }
    .scope-row .check { margin: 0; font-size: 13px; color: var(--ink); }
    .scope-count { width: 100%; }
    .scope-extra { font-size: 12px; color: var(--muted); }
    .scope-radius { width: 56px; display: inline-block; padding: 4px 6px; }
    .scope-total { font-size: 12px; color: var(--muted); margin-top: 2px; }
    .scope-total.over { color: var(--warn); font-weight: 700; }
    @media (max-width: 840px) {
      main { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
    }
  </style>
</head>
<body>
  <header><h1>Comp Study Deck Builder</h1></header>
  <main>
    <aside>
      <div class="form-group">
        <h2>Project brief</h2>
        <p class="hint">These are the fields the future frontend or RFP autofill should send to this agent.</p>
        <label>Project name</label>
        <input id="project_name" placeholder="e.g., 200 Vesey Repositioning">
        <label>Address</label>
        <input id="address" placeholder="e.g., 200 Vesey Street, New York, NY">
        <label>Program type</label>
        <input id="program_type" placeholder="e.g., office repositioning">
        <label>Scope summary</label>
        <textarea id="scope_summary" placeholder="e.g., Lobby, tenant amenity, retail, and public realm upgrades."></textarea>
        <label>Design priorities</label>
        <div id="design_priorities_container">
          <input type="text" id="design_priorities_0" placeholder="Design priority (e.g., arrival experience)" class="design-priority-input">
        </div>
      </div>
      <div class="form-group">
        <h2>Comparative projects</h2>
        <p class="hint">The agent searches on its own, then adds any must-use comps you provide.</p>
        <label>Comp guidance</label>
        <textarea id="comp_guidance" placeholder="e.g., Prioritize recent repositioning projects with strong arrival, amenity, and public realm moves."></textarea>
        <label>Comp types</label>
        <div id="comp_types_container">
          <input type="text" id="comp_types_0" placeholder="Comp type (e.g., office lobby repositioning)" class="comp-type-input">
        </div>
        <label>Comp scopes &amp; counts</label>
        <p class="hint">Pick how many comps to pull from each geographic scope. Leave blank to skip a scope. Total is capped at 50.</p>
        <div class="scope-grid">
          <div class="scope-row">
            <label class="check"><input id="scope_local_enabled" type="checkbox"> Local</label>
            <input id="scope_local_count" type="number" min="0" max="50" placeholder="count" class="scope-count">
            <span class="scope-extra">within <input id="radius_miles" type="number" min="0" step="0.5" placeholder="3" class="scope-radius"> mi</span>
          </div>
          <div class="scope-row">
            <label class="check"><input id="scope_national_enabled" type="checkbox"> National</label>
            <input id="scope_national_count" type="number" min="0" max="50" placeholder="count" class="scope-count">
            <span class="scope-extra">same country, outside local market</span>
          </div>
          <div class="scope-row">
            <label class="check"><input id="scope_international_enabled" type="checkbox"> International</label>
            <input id="scope_international_count" type="number" min="0" max="50" placeholder="count" class="scope-count">
            <span class="scope-extra">marquee global precedents</span>
          </div>
          <div class="scope-total">Total: <span id="scope_total">0</span> / 50</div>
        </div>
        <label>Must-use comps</label>
        <div id="must_include_comps_container">
          <input type="text" id="must_include_comps_0" placeholder="Project name | Location | Note (optional)" class="must-include-comp-input">
        </div>
      </div>
      <label>Time horizon (years)</label>
      <input id="time_horizon_years" type="number" min="1" placeholder="e.g., 8">
      <label>Search modes</label>
      <div class="checks">
        <label class="check"><input id="live_search" type="checkbox" checked> OpenAI live web</label>
        <label class="check"><input id="archive_sources" type="checkbox" checked disabled> Save sources</label>
      </div>
      <label>Output folder <span style="color: var(--warn);">(required)</span></label>
      <p class="hint">Decks save directly here. Must be an absolute path.</p>
      <div class="path-row">
        <input id="output_root" placeholder="e.g., C:\Users\Name\Desktop\Comp Outputs" required>
        <button id="browse_output" type="button" class="secondary">Browse</button>
      </div>
      <div class="actions">
        <button id="discover">Search comps</button>
        <button id="approve" class="secondary" disabled>Approve selected</button>
      </div>
      <p id="status" class="status">Ready.</p>
    </aside>
    <section>
      <div id="results" class="empty">Run a live search, approve comps, and generate the standardized Comp Study Deck.</div>
    </section>
  </main>
  <script>
    let lastPayload = null;
    let lastCandidates = [];
    const statusEl = document.getElementById('status');
    const resultsEl = document.getElementById('results');
    const approveBtn = document.getElementById('approve');

    function value(id) { return document.getElementById(id).value; }
    function checked(id) { return document.getElementById(id).checked; }
    function listValue(id) { return value(id).split(',').map(x => x.trim()).filter(Boolean); }
    function getLineValues(className) {
      const inputs = document.querySelectorAll('.' + className);
      return Array.from(inputs)
        .map(input => input.value.trim())
        .filter(value => value !== '');
    }
    function parseCompRows(className) {
      return getLineValues(className).map(row => {
        const parts = row.split('|').map(part => part.trim());
        return { name: parts[0] || '', location: parts[1] || '', note: parts[2] || '' };
      }).filter(item => item.name);
    }
    
    function getDynamicInputValues(className) {
      const inputs = document.querySelectorAll('.' + className);
      return Array.from(inputs)
        .map(input => input.value.trim())
        .filter(value => value !== '');
    }
    
    const SCOPE_KEYS = ['local', 'national', 'international'];
    const SCOPE_TOTAL_CAP = 50;
    function readScopeCounts() {
      const out = {};
      for (const scope of SCOPE_KEYS) {
        const enabled = checked(`scope_${scope}_enabled`);
        const raw = value(`scope_${scope}_count`).trim();
        const count = raw === '' ? 0 : Math.max(0, Math.floor(Number(raw)));
        if (enabled && count > 0) out[scope] = count;
      }
      return out;
    }
    function scopeTotal(scopeCounts) {
      return Object.values(scopeCounts).reduce((sum, n) => sum + n, 0);
    }
    function deriveGeography(address) {
      const parts = String(address || '').split(',').map(p => p.trim()).filter(Boolean);
      if (parts.length >= 2) return parts.slice(1).join(', ');
      return parts[0] || '';
    }
    function payload() {
      const compTypes = getDynamicInputValues('comp-type-input');
      const designPriorities = getDynamicInputValues('design-priority-input');
      const mustIncludeComps = parseCompRows('must-include-comp-input');
      const compsPerScope = readScopeCounts();
      const total = scopeTotal(compsPerScope);
      const address = value('address');
      return {
        project_name: value('project_name'),
        address: address,
        program_type: value('program_type'),
        scope_summary: value('scope_summary'),
        geography: deriveGeography(address),
        design_priorities: designPriorities,
        max_comps: total || undefined,
        comps_per_scope: compsPerScope,
        comp_types: compTypes.join(', '),
        amenity_priorities: designPriorities.join(', '),
        radius_miles: Number(value('radius_miles') || 3),
        time_horizon_years: Number(value('time_horizon_years') || 8),
        live_search: checked('live_search'),
        user_defined_comps: getLineValues('must-include-comp-input').join('\n'),
        output_root: value('output_root'),
        comparative_projects: {
          comp_guidance: value('comp_guidance'),
          comp_types: compTypes,
          must_include_comps: mustIncludeComps
        }
      };
    }
    function setupDynamicInputs(containerId, inputClass, placeholder) {
      const container = document.getElementById(containerId);
      
      function handleInput(event) {
        const inputs = container.querySelectorAll('.' + inputClass);
        const currentInput = event.target;
        const isLastInput = inputs[inputs.length - 1] === currentInput;
        const hasContent = currentInput.value.trim() !== '';
        
        if (isLastInput && hasContent) {
          const newInput = document.createElement('input');
          newInput.type = 'text';
          newInput.id = `${containerId}_${inputs.length}`;
          newInput.placeholder = placeholder;
          newInput.className = inputClass;
          newInput.addEventListener('input', handleInput);
          container.appendChild(newInput);
        }
        
        const allInputs = container.querySelectorAll('.' + inputClass);
        for (let i = allInputs.length - 2; i >= 0; i--) {
          if (allInputs[i].value.trim() === '') {
            container.removeChild(allInputs[i]);
          }
        }
      }
      
      const existingInputs = container.querySelectorAll('.' + inputClass);
      existingInputs.forEach(input => {
        input.addEventListener('input', handleInput);
      });
    }
    
    function setupMustIncludeCompsInputs() {
      setupDynamicInputs('must_include_comps_container', 'must-include-comp-input', 'Project name | Location | Note (optional)');
    }

    function setupCompTypesInputs() {
      setupDynamicInputs('comp_types_container', 'comp-type-input', 'Comp type (e.g., adaptive reuse)');
    }
    
    function setupDesignPrioritiesInputs() {
      setupDynamicInputs('design_priorities_container', 'design-priority-input', 'Design priority (e.g., arrival experience)');
    }
    
    function setBusy(isBusy, text, actionText = 'Searching...') {
      const discoverBtn = document.getElementById('discover');

      const total = scopeTotal(readScopeCounts());
      discoverBtn.disabled = isBusy || total <= 0;
      approveBtn.disabled = isBusy || !lastCandidates.length;
      statusEl.textContent = text;

      if (isBusy) {
        discoverBtn.innerHTML = `<div class="button-loading"><div class="loading"></div>${escapeHtml(actionText)}</div>`;
        discoverBtn.classList.add('button-loading');
      } else {
        discoverBtn.innerHTML = 'Search comps';
        discoverBtn.classList.remove('button-loading');
      }
    }
    function refreshScopeTotal() {
      const counts = readScopeCounts();
      const total = scopeTotal(counts);
      const totalEl = document.getElementById('scope_total');
      const wrapper = totalEl ? totalEl.parentElement : null;
      if (totalEl) totalEl.textContent = String(total);
      if (wrapper) wrapper.classList.toggle('over', total > SCOPE_TOTAL_CAP);
      const discoverBtn = document.getElementById('discover');
      const isBusy = discoverBtn.classList.contains('button-loading');
      if (!isBusy) {
        discoverBtn.disabled = total <= 0 || total > SCOPE_TOTAL_CAP;
        if (total <= 0) {
          statusEl.textContent = 'Enter a count for at least one scope to enable search.';
        } else if (total > SCOPE_TOTAL_CAP) {
          statusEl.textContent = `Total ${total} exceeds the ${SCOPE_TOTAL_CAP}-comp cap. Reduce one or more counts.`;
        } else if (statusEl.textContent.startsWith('Enter a count') || statusEl.textContent.startsWith('Total ')) {
          statusEl.textContent = 'Ready.';
        }
      }
    }
    function bindScopeInputs() {
      for (const scope of SCOPE_KEYS) {
        const checkbox = document.getElementById(`scope_${scope}_enabled`);
        const countInput = document.getElementById(`scope_${scope}_count`);
        countInput.addEventListener('input', () => {
          if (countInput.value.trim() !== '' && Number(countInput.value) > 0) {
            checkbox.checked = true;
          } else if (countInput.value.trim() === '') {
            checkbox.checked = false;
          }
          refreshScopeTotal();
        });
        checkbox.addEventListener('change', () => {
          if (!checkbox.checked) {
            countInput.value = '';
          } else if (countInput.value.trim() === '') {
            countInput.focus();
          }
          refreshScopeTotal();
        });
      }
    }
    async function post(url, data) {
      const res = await fetch(url, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
      if (!res.ok) throw new Error(await res.text());
      return await res.json();
    }
    async function selectOutputFolder() {
      statusEl.textContent = 'Opening folder picker...';
      const data = await post('/api/select-output-folder', {});
      if (data.path) {
        document.getElementById('output_root').value = data.path;
        statusEl.textContent = 'Output folder selected.';
      } else {
        statusEl.textContent = 'No output folder selected.';
      }
    }
    function bannerHtml(status) {
      if (!status || !status.headline) return '';
      return `<div class="banner"><h3>${escapeHtml(status.headline)}</h3><p>${escapeHtml(status.detail || '')}</p></div>`;
    }
    function renderResults(data) {
      lastPayload = data.brief;
      lastPayload.output_root = data.output_root;
      lastPayload.live_search = checked('live_search');
      lastCandidates = data.candidates || [];
      approveBtn.disabled = !lastCandidates.length;
      const banner = bannerHtml(data.live_search_status);
      if (!lastCandidates.length) {
        resultsEl.className = '';
        const fallback = banner
          ? ''
          : '<div class="banner"><h3>No Candidates Returned</h3><p>The search completed but no comps were produced. Try widening the brief or adding must-use comps.</p></div>';
        resultsEl.innerHTML = banner + fallback;
        return;
      }
      resultsEl.className = '';
      const candidates = lastCandidates.map((c, i) => {
        const attrs = c.known_attributes || {};
        const takeaway = attrs.presentation_takeaway || (c.source_notes || []).join(' ') || c.quick_reason || 'Review source notes before approval.';
        const sourceLabel = c.candidate_source === 'user_defined' ? 'Source: Must-use comp' : 
                           c.candidate_source === 'user_defined_and_live' ? 'Source: Must-use comp + live search' : 
                           'Source: Live search';
        const userNote = c.user_note ? `<div class="meta">Note: ${escapeHtml(c.user_note)}</div>` : '';
        return `<article class="candidate">
          <input type="checkbox" class="candidate-check" value="${c.comp_id}" checked>
          <div>
            <h3>${i + 1}. ${escapeHtml(c.comp_name || c.project_name)}</h3>
            <div class="meta">${escapeHtml(sourceLabel)} | ${escapeHtml(c.comp_type)} | ${escapeHtml(c.location)}</div>
            <div class="takeaway">${escapeHtml(takeaway)}</div>
            ${userNote}
          </div>
        </article>`;
      }).join('');
      const sources = (data.source_log || []).slice(0, 12).map(s => `<div class="source">${escapeHtml(s.source_name)} · <a href="${s.url_or_search}" target="_blank">${escapeHtml(s.source_type)}</a></div>`).join('');
      resultsEl.innerHTML = `${banner}<h2>Candidate comps</h2>${candidates}<div class="sources"><h2>Sources</h2>${sources || '<div class="source">No source URLs captured.</div>'}</div>`;
    }
    function escapeHtml(text) {
      return String(text || '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[ch]));
    }
    function showProgress(title, job) {
      const percent = Math.max(0, Math.min(100, Number(job.percent || 0)));
      const elapsed = Number(job.elapsed_seconds || 0).toFixed(1);
      const wasEmpty = resultsEl.className === 'empty';
      const html = `<div class="progress" id="progress">
        <div class="progress-top">
          <strong>${escapeHtml(title)}</strong>
          <span>${percent}% · ${elapsed}s</span>
        </div>
        <div class="progress-bar"><div class="progress-fill" style="width:${percent}%"></div></div>
        <div class="progress-message">${escapeHtml(job.message || 'Working...')}</div>
      </div>`;
      const existing = document.getElementById('progress');
      if (existing) {
        existing.outerHTML = html;
      } else {
        resultsEl.className = '';
        resultsEl.innerHTML = html + (wasEmpty ? '' : resultsEl.innerHTML);
      }
    }
    async function runJob(startUrl, data, title, onStarted) {
      const started = await post(startUrl, data);
      if (typeof onStarted === 'function') {
        try { onStarted(); } catch (err) { /* best-effort hook; never block the run */ }
      }
      const statusUrl = started.status_url;
      while (true) {
        const res = await fetch(statusUrl);
        if (!res.ok) throw new Error(await res.text());
        const job = await res.json();
        showProgress(title, job);
        statusEl.textContent = `${job.message || 'Working...'} · ${Number(job.elapsed_seconds || 0).toFixed(1)}s`;
        if (job.status === 'complete') return job.result;
        if (job.status === 'failed') {
          const failure = new Error(job.error || 'Job failed.');
          failure.friendly = job.friendly_error || null;
          throw failure;
        }
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
    }
    async function saveSettings() {
      try {
        await post('/api/settings', {
          output_root: value('output_root').trim(),
          live_search: checked('live_search'),
        });
      } catch (err) { /* best-effort; never block a run on settings persistence */ }
    }
    async function initPreflight() {
      let settings = {};
      try {
        const res = await fetch('/api/settings');
        if (res.ok) settings = (await res.json()).settings || {};
      } catch (err) { /* missing or corrupt settings must never break startup */ }
      let preflight = null;
      try {
        const res = await fetch('/api/preflight');
        if (res.ok) preflight = await res.json();
      } catch (err) { /* preflight is advisory; the form still works without it */ }
      const outputInput = document.getElementById('output_root');
      if (!outputInput.value.trim()) {
        const saved = String(settings.output_root || '').trim();
        const fallback = preflight && preflight.default_output_root ? String(preflight.default_output_root) : '';
        outputInput.value = saved || fallback;
      }
      if (typeof settings.live_search === 'boolean') {
        document.getElementById('live_search').checked = settings.live_search;
      }
      if (preflight && preflight.friendly_error) {
        resultsEl.className = '';
        resultsEl.innerHTML = bannerHtml(preflight.friendly_error);
        statusEl.textContent = preflight.friendly_error.headline;
      }
    }
    // Initialize all dynamic inputs
    setupMustIncludeCompsInputs();
    setupCompTypesInputs();
    setupDesignPrioritiesInputs();
    bindScopeInputs();
    refreshScopeTotal();
    initPreflight();
    document.getElementById('browse_output').addEventListener('click', async () => {
      try {
        await selectOutputFolder();
      } catch (err) {
        statusEl.textContent = 'Folder picker unavailable. Enter a path manually.';
      }
    });
    
    function validateOutputRoot() {
      const raw = value('output_root').trim();
      if (!raw) {
        statusEl.textContent = 'Pick an output folder before searching.';
        document.getElementById('output_root').focus();
        return false;
      }
      // Windows product: drive-letter or UNC paths only. A bare '/x' path
      // passes Path.is_absolute() on POSIX but NOT on Windows, so accepting
      // it client-side would let the job start and then fail server-side.
      const isAbsolute = /^[a-zA-Z]:[\\/]/.test(raw) || raw.startsWith('\\\\');
      if (!isAbsolute) {
        statusEl.textContent = 'Output folder must be an absolute path (e.g., C:\\Comp Outputs).';
        document.getElementById('output_root').focus();
        return false;
      }
      return true;
    }

    document.getElementById('discover').addEventListener('click', async () => {
      if (!validateOutputRoot()) return;
      const compsPerScope = readScopeCounts();
      const total = scopeTotal(compsPerScope);
      if (total <= 0) {
        statusEl.textContent = 'Enter a count for at least one scope to enable search.';
        return;
      }
      if (total > SCOPE_TOTAL_CAP) {
        statusEl.textContent = `Total ${total} exceeds the ${SCOPE_TOTAL_CAP}-comp cap. Reduce one or more counts.`;
        return;
      }
      setBusy(true, 'Starting search...', 'Searching...');
      try {
        const data = await runJob('/api/discover/start', payload(), 'Searching Comps', saveSettings);
        renderResults(data);
        setBusy(false, `Found ${lastCandidates.length} candidates. Select comps to approve.`);
      } catch (err) {
        const friendly = err.friendly || { headline: 'Search Failed', detail: (err.message || '').split('\n')[0].slice(0, 240) };
        setBusy(false, friendly.headline);
        resultsEl.className = '';
        resultsEl.innerHTML = bannerHtml(friendly);
      }
    });
    approveBtn.addEventListener('click', async () => {
      const approveBtn = document.getElementById('approve');
      const originalText = approveBtn.innerHTML;
      const selectedIds = Array.from(document.querySelectorAll('.candidate-check:checked')).map(cb => cb.value);
      
      if (!selectedIds.length) {
        statusEl.textContent = 'Select at least one comp.';
        return;
      }
      
      setBusy(true, 'Starting deck generation...', 'Generating...');
      approveBtn.innerHTML = '<div class="button-loading"><div class="loading"></div>Approving...</div>';
      approveBtn.classList.add('button-loading');
      
      try {
        const data = await runJob('/api/approve/start', {
          brief: lastPayload,
          comp_ids: selectedIds,
          output_root: lastPayload.output_root,
          live_search: lastPayload.live_search,
        }, 'Generating Deck');
        setBusy(false, `Approved ${selectedIds.length} comps. Generating deck...`);
        approveBtn.innerHTML = originalText;
        approveBtn.classList.remove('button-loading');
        
        const paths = data.paths || {};
        const strategy = data.deck_strategy || {};
        const outputHtml = `<div class="output">
          <h2>Comp Study Deck generated</h2>
          <div class="output-grid">
            <div>PPTX</div><div><code>${escapeHtml(paths.comp_study_deck || paths.poc_deck || '')}</code></div>
            <div>Deck data</div><div><code>${escapeHtml(paths.deck_data || '')}</code></div>
            <div>Strategy</div><div><code>${escapeHtml(paths.deck_strategy || '')}</code></div>
            <div>Normalized comps</div><div><code>${escapeHtml(paths.approved_comps_normalized || '')}</code></div>
            <div>Deck structure</div><div>${escapeHtml(strategy.deck_title || 'Comp Study Deck')} · ${escapeHtml(strategy.project_type_label || '')}</div>
          </div>
        </div>`;
        resultsEl.insertAdjacentHTML('afterbegin', outputHtml);
      } catch (err) {
        const friendly = err.friendly || { headline: 'Deck Generation Failed', detail: (err.message || '').split('\n')[0].slice(0, 240) };
        setBusy(false, friendly.headline);
        approveBtn.innerHTML = originalText;
        approveBtn.classList.remove('button-loading');
        resultsEl.insertAdjacentHTML('afterbegin', bannerHtml(friendly));
      }
    });
  </script>
</body>
</html>
"""
