"""WS-3 tests: preflight endpoint, per-user defaults, and settings persistence.

These are additive — they exercise the new /api/preflight and /api/settings
endpoints plus their helpers, and assert that no secret values ever appear in
any payload.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from comp_agent import config, ui


SECRET = "sk-test-super-secret-value-1234567890"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Keep tests off the real network share, real env, and real user profile.

    - Snapshots/restores os.environ so resolution side effects don't leak.
    - Empties SHARED_CONFIG_FILES so no test probes the real UNC path.
    - Points LOCALAPPDATA at a tmp dir so the real settings.json is untouched.
    - Runs in a tmp working directory so the repo .env can't interfere.
    """
    saved = os.environ.copy()
    monkeypatch.setattr(config, "SHARED_CONFIG_FILES", ())
    monkeypatch.delenv("COMP_AGENT_CONFIG", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.chdir(tmp_path)
    # The preflight's OpenAI reachability probe is a real network call; tests
    # must stay deterministic and offline-safe.
    monkeypatch.setattr(ui, "_openai_reachable", lambda *args, **kwargs: True)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture()
def ui_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), ui._handler_for("projects_ui"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


# Loopback requests must never route through a system/corporate proxy, or
# these tests fail on any machine with a registry proxy that doesn't bypass
# 127.0.0.1 (the Windows '<local>' rule does not match dotted hosts).
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_get(base: str, path: str) -> tuple[int, str]:
    with _NO_PROXY.open(base + path, timeout=10) as res:
        return res.status, res.read().decode("utf-8")


def _http_post(base: str, path: str, payload: dict) -> tuple[int, str]:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _NO_PROXY.open(request, timeout=10) as res:
        return res.status, res.read().decode("utf-8")


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def test_preflight_endpoint_shape_and_no_secrets(monkeypatch, ui_server):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    status, body = _http_get(ui_server, "/api/preflight")
    assert status == 200
    payload = json.loads(body)

    assert payload["ok"] is True
    assert payload["openai_key_present"] is True
    assert payload["key_source"] == "env"
    assert payload["share_reachable"] is None  # share layer emptied in tests
    assert payload["default_output_root"] == str(Path.home() / "Documents" / "Comp Packages")
    assert payload["friendly_error"] is None
    assert isinstance(payload["layers"], list)
    # The key VALUE must never appear anywhere in the payload.
    assert SECRET not in body


def test_preflight_reports_key_source_from_explicit_config(monkeypatch, tmp_path):
    cfg = tmp_path / "office.json"
    cfg.write_text(json.dumps({"OPENAI_API_KEY": SECRET}), encoding="utf-8")
    monkeypatch.setenv("COMP_AGENT_CONFIG", str(cfg))

    report = ui._preflight_report()

    assert report["ok"] is True
    assert report["openai_key_present"] is True
    assert report["key_source"] == "explicit"
    assert report["friendly_error"] is None
    assert SECRET not in json.dumps(report)


def test_preflight_no_key_shows_admin_message():
    report = ui._preflight_report()

    assert report["ok"] is False
    assert report["openai_key_present"] is False
    assert report["key_source"] is None
    friendly = report["friendly_error"]
    assert friendly["headline"] == "No OpenAI Key Found"
    assert "admin" in friendly["detail"].lower()


def test_preflight_unreachable_share_shows_vpn_message(monkeypatch):
    monkeypatch.setattr(
        config, "SHARED_CONFIG_FILES", (r"\\unreachable\share\comp_agent.env",)
    )
    monkeypatch.setattr(
        config, "_read_file_with_timeout", lambda path, timeout: (None, "timeout")
    )

    report = ui._preflight_report()

    assert report["ok"] is False
    assert report["share_reachable"] is False
    friendly = report["friendly_error"]
    assert friendly["headline"] == "Can't Reach the Shared Key Config"
    assert "vpn" in friendly["detail"].lower()


def test_preflight_fast_share_failure_also_shows_vpn_message(monkeypatch):
    """Off-VPN DNS failures fail FAST, not by timeout — same friendly copy."""
    monkeypatch.setattr(
        config, "SHARED_CONFIG_FILES", (r"\\unreachable\share\comp_agent.env",)
    )
    monkeypatch.setattr(
        config, "_read_file_with_timeout", lambda path, timeout: (None, "unreachable")
    )

    report = ui._preflight_report()

    assert report["ok"] is False
    assert report["share_reachable"] is False
    assert report["friendly_error"]["headline"] == "Can't Reach the Shared Key Config"


def test_preflight_openai_unreachable_shows_network_banner(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setattr(ui, "_openai_reachable", lambda *args, **kwargs: False)

    report = ui._preflight_report()

    assert report["openai_key_present"] is True
    assert report["openai_reachable"] is False
    assert report["ok"] is False
    friendly = report["friendly_error"]
    assert friendly["headline"] == "Can't Reach OpenAI"
    assert SECRET not in json.dumps(report)


def test_preflight_does_not_mutate_environment(monkeypatch, tmp_path):
    """A page-load preflight must never write env vars into an in-flight job."""
    cfg = tmp_path / "office.json"
    cfg.write_text(
        json.dumps({"OPENAI_API_KEY": SECRET, "COMP_AGENT_LIVE_SEARCH": "1"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("COMP_AGENT_CONFIG", str(cfg))
    monkeypatch.delenv("COMP_AGENT_LIVE_SEARCH", raising=False)

    report = ui._preflight_report()

    # The report sees the key, but the process env stays untouched.
    assert report["openai_key_present"] is True
    assert "OPENAI_API_KEY" not in os.environ
    assert "COMP_AGENT_LIVE_SEARCH" not in os.environ


def test_classify_output_folder_errors():
    friendly = ui._classify_error("ValueError: Output folder must be an absolute path. Got: projects_ui")
    assert friendly["headline"] == "Output Folder Problem"
    friendly = ui._classify_error("ValueError: Output folder is required. Pick a destination folder before running.")
    assert friendly["headline"] == "Output Folder Problem"


def test_key_source_derivation_from_report_shapes():
    assert ui._key_source_from_report(
        {"layers": [{"layer": "shared", "keys_set": ["OPENAI_API_KEY"]}], "openai_key_present": True}
    ) == "shared"
    # Startup already loaded the key: the file layer that carries it wins.
    assert ui._key_source_from_report(
        {
            "layers": [
                {"layer": "app_dir", "keys_set": [], "keys_already_in_env": []},
                {"layer": "dotenv", "keys_set": [], "keys_already_in_env": ["OPENAI_API_KEY"]},
            ],
            "openai_key_present": True,
        }
    ) == "dotenv"
    assert ui._key_source_from_report({"layers": [], "openai_key_present": True}) == "env"
    assert ui._key_source_from_report({"layers": [], "openai_key_present": False}) is None


def test_classify_error_share_and_key_copy():
    # "timed out" text must not hijack the share-specific classification.
    share = ui._classify_error("Shared key config unreachable: share probe timed out")
    assert share["headline"] == "Can't Reach the Shared Key Config"
    assert "vpn" in share["detail"].lower()

    missing = ui._classify_error("No OpenAI key found in any config location")
    assert missing["headline"] == "No OpenAI Key Found"
    assert "admin" in missing["detail"].lower()

    # Job-time failures of the same nature get the same friendly copy.
    job_time = ui._classify_error("RuntimeError: OPENAI_API_KEY is not set; live search skipped")
    assert job_time == missing


# ---------------------------------------------------------------------------
# Default output folder
# ---------------------------------------------------------------------------

def test_default_output_root_is_documents_comp_packages(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(ui.Path, "home", classmethod(lambda cls: fake_home))

    assert ui._default_output_root() == str(fake_home / "Documents" / "Comp Packages")
    # Computed only — the folder must not be created until a run starts.
    assert not (fake_home / "Documents").exists()


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------

def test_settings_round_trip_drops_unknown_keys(tmp_path):
    saved = ui._save_settings(
        {"output_root": r"C:\Somewhere\Out", "live_search": False, "junk": "drop-me"}
    )
    assert saved == {"output_root": r"C:\Somewhere\Out", "live_search": False}
    assert ui._load_settings() == saved
    expected_path = tmp_path / "localappdata" / "CompAgent" / "settings.json"
    assert ui._settings_path() == expected_path
    assert expected_path.is_file()
    assert "junk" not in expected_path.read_text(encoding="utf-8")


def test_settings_merge_preserves_other_keys():
    ui._save_settings({"output_root": r"C:\First", "live_search": True})
    ui._save_settings({"live_search": False})
    assert ui._load_settings() == {"output_root": r"C:\First", "live_search": False}


def test_corrupt_settings_file_falls_back_to_defaults():
    path = ui._settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    assert ui._load_settings() == {}


def test_wrong_typed_settings_values_are_ignored():
    path = ui._settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"output_root": 123, "live_search": "yes"}), encoding="utf-8")
    assert ui._load_settings() == {}


def test_settings_path_falls_back_to_home_without_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    fake_home = tmp_path / "home"
    monkeypatch.setattr(ui.Path, "home", classmethod(lambda cls: fake_home))
    assert ui._settings_path() == fake_home / "CompAgent" / "settings.json"


def test_settings_endpoints_round_trip(ui_server):
    status, body = _http_post(
        ui_server,
        "/api/settings",
        {"output_root": r"C:\Comp Outputs", "live_search": True, "junk": 1},
    )
    assert status == 200
    saved = json.loads(body)
    assert saved["ok"] is True
    assert saved["settings"] == {"output_root": r"C:\Comp Outputs", "live_search": True}

    status, body = _http_get(ui_server, "/api/settings")
    assert status == 200
    loaded = json.loads(body)
    assert loaded["settings"] == {"output_root": r"C:\Comp Outputs", "live_search": True}
    assert loaded["path"].endswith("settings.json")


def test_index_html_wires_new_endpoints():
    assert "/api/preflight" in ui.INDEX_HTML
    assert "/api/settings" in ui.INDEX_HTML
    assert "saveSettings" in ui.INDEX_HTML
    assert "initPreflight" in ui.INDEX_HTML
