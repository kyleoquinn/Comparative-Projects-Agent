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
            if self.path == "/api/discover":
                self._handle_discover(default_output_root)
                return
            if self.path == "/api/approve":
                self._handle_approve(default_output_root)
                return
            self.send_error(404)

        def _handle_discover(self, default_output_root: str) -> None:
            payload = self._read_json()
            self._send_json(_run_discover(payload, default_output_root))

        def _handle_approve(self, default_output_root: str) -> None:
            payload = self._read_json()
            self._send_json(_run_approve(payload, default_output_root))

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


def _run_discover(payload: dict[str, Any], default_output_root: str, update: JobUpdate | None = None) -> dict[str, Any]:
    def progress(message: str, percent: int) -> None:
        if update:
            update(message, percent)

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
    output_root = str(payload.get("output_root") or default_output_root)

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
    progress("Candidate search complete", 100)
    return {
        "brief": to_jsonable(brief),
        "output_root": output_root,
        "paths": {key: str(value) for key, value in paths.items()},
        "candidates": candidates,
        "source_log": source_log,
    }


def _run_approve(payload: dict[str, Any], default_output_root: str, update: JobUpdate | None = None) -> dict[str, Any]:
    def progress(message: str, percent: int) -> None:
        if update:
            update(message, percent)

    progress("Reading approved comp selection", 5)
    brief = _brief_from_payload(payload.get("brief") or payload)
    output_root = str(payload.get("output_root") or default_output_root)
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
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job.update(
            {
                "status": "failed",
                "message": "Failed",
                "updated_at": now,
                "elapsed_seconds": round(now - float(job["created_at"]), 1),
                "error": f"{error}\n{traceback.format_exc()}",
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
        <div class="row">
          <div>
            <label>Geography</label>
            <input id="geography" placeholder="e.g., New York City">
          </div>
          <div>
            <label>Number of comps</label>
            <input id="max_comps" type="number" min="1" max="12" placeholder="e.g., 5">
          </div>
        </div>
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
        <label>Must-use comps</label>
        <div id="must_include_comps_container">
          <input type="text" id="must_include_comps_0" placeholder="Project name | Location | Note (optional)" class="must-include-comp-input">
        </div>
      </div>
      <div class="row">
        <div>
          <label>Radius miles</label>
          <input id="radius_miles" type="number" min="0" step="0.5" placeholder="e.g., 3">
        </div>
        <div>
          <label>Time horizon</label>
          <input id="time_horizon_years" type="number" min="1" placeholder="e.g., 8">
        </div>
      </div>
      <label>Search modes</label>
      <div class="checks">
        <label class="check"><input id="live_search" type="checkbox" checked> OpenAI live web</label>
        <label class="check"><input id="archive_sources" type="checkbox" checked disabled> Save sources</label>
      </div>
      <label>Output root</label>
      <div class="path-row">
        <input id="output_root" placeholder="e.g., C:\Users\Name\Desktop\Comp Outputs">
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
    
    function payload() {
      const compTypes = getDynamicInputValues('comp-type-input');
      const designPriorities = getDynamicInputValues('design-priority-input');
      const mustIncludeComps = parseCompRows('must-include-comp-input');
      return {
        project_name: value('project_name'),
        address: value('address'),
        program_type: value('program_type'),
        scope_summary: value('scope_summary'),
        geography: value('geography'),
        design_priorities: designPriorities,
        max_comps: Number(value('max_comps') || 5),
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
      
      document.getElementById('discover').disabled = isBusy;
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
    function renderResults(data) {
      lastPayload = data.brief;
      lastPayload.output_root = data.output_root;
      lastPayload.live_search = checked('live_search');
      lastCandidates = data.candidates || [];
      approveBtn.disabled = !lastCandidates.length;
      if (!lastCandidates.length) {
        resultsEl.className = 'empty';
        const log = (data.source_log || []).map(s => `${s.status}: ${s.notes || s.source_name}`).join('\n');
        const isTimeout = log.toLowerCase().includes('timed out');
        const errorMsg = isTimeout 
          ? 'Live search timed out. Try a lighter search, fewer comps, or add comps manually.'
          : 'No candidate comps returned.';
        resultsEl.textContent = `${errorMsg}\n\n${log || 'Check source log and search settings.'}`;
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
      resultsEl.innerHTML = `<h2>Candidate comps</h2>${candidates}<div class="sources"><h2>Sources</h2>${sources || '<div class="source">No source URLs captured.</div>'}</div>`;
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
    async function runJob(startUrl, data, title) {
      const started = await post(startUrl, data);
      const statusUrl = started.status_url;
      while (true) {
        const res = await fetch(statusUrl);
        if (!res.ok) throw new Error(await res.text());
        const job = await res.json();
        showProgress(title, job);
        statusEl.textContent = `${job.message || 'Working...'} · ${Number(job.elapsed_seconds || 0).toFixed(1)}s`;
        if (job.status === 'complete') return job.result;
        if (job.status === 'failed') throw new Error(job.error || 'Job failed.');
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
    }
    // Initialize all dynamic inputs
    setupMustIncludeCompsInputs();
    setupCompTypesInputs();
    setupDesignPrioritiesInputs();
    document.getElementById('browse_output').addEventListener('click', async () => {
      try {
        await selectOutputFolder();
      } catch (err) {
        statusEl.textContent = 'Folder picker unavailable. Enter a path manually.';
      }
    });
    
    document.getElementById('discover').addEventListener('click', async () => {
      setBusy(true, 'Starting search...', 'Searching...');
      try {
        const data = await runJob('/api/discover/start', payload(), 'Searching Comps');
        renderResults(data);
        setBusy(false, `Found ${lastCandidates.length} candidates. Select comps to approve.`);
      } catch (err) {
        setBusy(false, 'Search failed.');
        resultsEl.className = 'empty';
        resultsEl.textContent = err.message;
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
        setBusy(false, 'Approval failed.');
        approveBtn.innerHTML = originalText;
        approveBtn.classList.remove('button-loading');
        resultsEl.insertAdjacentHTML('afterbegin', `<p class="meta">${escapeHtml(err.message)}</p>`);
      }
    });
  </script>
</body>
</html>
"""
