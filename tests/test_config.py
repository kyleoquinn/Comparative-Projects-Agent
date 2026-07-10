from __future__ import annotations

import json
import os
import sys
import time

import pytest

from comp_agent import cli, config


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Keep tests off the real network share, real env vars, and the repo CWD.

    - Snapshots/restores os.environ so keys set by resolution don't leak.
    - Empties SHARED_CONFIG_FILES so no test ever probes the real UNC path.
    - Clears COMP_AGENT_CONFIG and runs in a tmp working directory so the
      repo-local .env / config files can't interfere.
    """
    saved = os.environ.copy()
    monkeypatch.setattr(config, "SHARED_CONFIG_FILES", ())
    monkeypatch.delenv("COMP_AGENT_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    yield
    os.environ.clear()
    os.environ.update(saved)


def _layers(report: dict, layer: str) -> list[dict]:
    return [entry for entry in report["layers"] if entry["layer"] == layer]


def test_process_env_beats_config_file(monkeypatch, tmp_path):
    monkeypatch.setenv("COMP_TEST_ALPHA", "from-process-env")
    cfg = tmp_path / "explicit.json"
    cfg.write_text(
        json.dumps({"COMP_TEST_ALPHA": "from-file", "COMP_TEST_BETA": "beta-from-file"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("COMP_AGENT_CONFIG", str(cfg))

    report = config.resolve_config()

    assert os.environ["COMP_TEST_ALPHA"] == "from-process-env"
    assert os.environ["COMP_TEST_BETA"] == "beta-from-file"
    explicit = _layers(report, "explicit")[0]
    assert explicit["status"] == "loaded"
    assert explicit["format"] == "json"
    assert "COMP_TEST_ALPHA" in explicit["keys_already_in_env"]
    assert "COMP_TEST_BETA" in explicit["keys_set"]


def test_comp_agent_config_env_format(monkeypatch, tmp_path):
    cfg = tmp_path / "office.env"
    cfg.write_text(
        '# shared office config\nCOMP_TEST_GAMMA="quoted value"\nCOMP_TEST_DELTA=\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("COMP_AGENT_CONFIG", str(cfg))

    report = config.resolve_config()

    assert os.environ["COMP_TEST_GAMMA"] == "quoted value"
    assert os.environ["COMP_TEST_DELTA"] == ""
    explicit = _layers(report, "explicit")[0]
    assert explicit["status"] == "loaded"
    assert explicit["format"] == "env"


def test_app_dir_json_beats_app_dir_env(tmp_path):
    (tmp_path / "comp_agent.config.json").write_text(
        json.dumps({"COMP_TEST_K1": "from-json"}), encoding="utf-8"
    )
    (tmp_path / "comp_agent.env").write_text(
        "COMP_TEST_K1=from-env\nCOMP_TEST_K2=env-only\n", encoding="utf-8"
    )

    config.resolve_config()

    assert os.environ["COMP_TEST_K1"] == "from-json"
    assert os.environ["COMP_TEST_K2"] == "env-only"


def test_frozen_app_dir_uses_executable_directory(monkeypatch, tmp_path):
    exe_dir = tmp_path / "bundle"
    exe_dir.mkdir()
    (exe_dir / "comp_agent.config.json").write_text(
        json.dumps({"COMP_TEST_FROZEN": "yes"}), encoding="utf-8"
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "CompAgent.exe"))

    config.resolve_config()

    assert os.environ["COMP_TEST_FROZEN"] == "yes"


def test_shared_default_layer_loads(monkeypatch, tmp_path):
    shared = tmp_path / "share"
    shared.mkdir()
    shared_json = shared / "comp_agent.config.json"
    shared_json.write_text(json.dumps({"COMP_TEST_SHARED": "from-share"}), encoding="utf-8")
    monkeypatch.setattr(
        config,
        "SHARED_CONFIG_FILES",
        (str(shared_json), str(shared / "comp_agent.env")),
    )

    report = config.resolve_config()

    assert os.environ["COMP_TEST_SHARED"] == "from-share"
    assert report["share_reachable"] is True


def test_comp_agent_config_replaces_shared_lookup(monkeypatch, tmp_path):
    shared_json = tmp_path / "share.json"
    shared_json.write_text(json.dumps({"COMP_TEST_SH": "share"}), encoding="utf-8")
    monkeypatch.setattr(config, "SHARED_CONFIG_FILES", (str(shared_json),))
    explicit = tmp_path / "explicit.env"
    explicit.write_text("COMP_TEST_EX=explicit\n", encoding="utf-8")
    monkeypatch.setenv("COMP_AGENT_CONFIG", str(explicit))

    report = config.resolve_config()

    assert os.environ["COMP_TEST_EX"] == "explicit"
    assert "COMP_TEST_SH" not in os.environ
    assert report["share_reachable"] is None
    assert all(entry["status"] == "skipped" for entry in _layers(report, "shared"))


def test_layer_precedence_first_hit_wins(monkeypatch, tmp_path):
    explicit = tmp_path / "explicit.env"
    explicit.write_text("COMP_TEST_P1=explicit\n", encoding="utf-8")
    monkeypatch.setenv("COMP_AGENT_CONFIG", str(explicit))
    (tmp_path / "comp_agent.env").write_text(
        "COMP_TEST_P1=app-dir\nCOMP_TEST_P2=app-dir\n", encoding="utf-8"
    )
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "COMP_TEST_P1=dotenv\nCOMP_TEST_P2=dotenv\nCOMP_TEST_P3=dotenv\n",
        encoding="utf-8",
    )

    config.resolve_config(dotenv_path=dotenv)

    assert os.environ["COMP_TEST_P1"] == "explicit"
    assert os.environ["COMP_TEST_P2"] == "app-dir"
    assert os.environ["COMP_TEST_P3"] == "dotenv"


def test_dotenv_dev_fallback_still_loads(tmp_path):
    (tmp_path / ".env").write_text("COMP_TEST_DEV=dev\n", encoding="utf-8")

    report = config.resolve_config()

    assert os.environ["COMP_TEST_DEV"] == "dev"
    assert _layers(report, "dotenv")[0]["status"] == "loaded"


def test_missing_explicit_path_is_tolerated(monkeypatch, tmp_path):
    monkeypatch.setenv("COMP_AGENT_CONFIG", str(tmp_path / "nope" / "missing.json"))

    report = config.resolve_config()

    assert _layers(report, "explicit")[0]["status"] == "missing"


def test_malformed_explicit_path_never_raises(monkeypatch):
    monkeypatch.setenv("COMP_AGENT_CONFIG", '??:*<>|"bad')

    report = config.resolve_config()

    assert _layers(report, "explicit")[0]["status"] in {"missing", "timeout"}


def test_malformed_json_is_tolerated_and_resolution_continues(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("COMP_AGENT_CONFIG", str(bad))
    dotenv = tmp_path / ".env"
    dotenv.write_text("COMP_TEST_AFTER_BAD=still-loads\n", encoding="utf-8")

    report = config.resolve_config(dotenv_path=dotenv)

    assert _layers(report, "explicit")[0]["status"] == "parse-error"
    assert os.environ["COMP_TEST_AFTER_BAD"] == "still-loads"


def test_unreachable_share_skipped_after_timeout(monkeypatch):
    unreachable = (
        r"\\no-such-host\share\comp_agent.config.json",
        r"\\no-such-host\share\comp_agent.env",
    )
    monkeypatch.setattr(config, "SHARED_CONFIG_FILES", unreachable)
    monkeypatch.setattr(config, "NETWORK_PROBE_TIMEOUT_SECONDS", 0.05)

    def fake_read(path):
        if str(path).startswith("\\\\no-such-host"):
            time.sleep(0.5)  # simulate a hanging UNC probe
        return None

    monkeypatch.setattr(config, "_read_file", fake_read)

    start = time.monotonic()
    report = config.resolve_config()
    elapsed = time.monotonic() - start

    assert report["share_reachable"] is False
    shared_entries = _layers(report, "shared")
    assert shared_entries[0]["status"] == "timeout"
    assert shared_entries[1]["status"] == "skipped"
    assert elapsed < 0.4  # the sibling file was not probed after the timeout


def test_json_scalar_values_are_coerced(monkeypatch, tmp_path):
    cfg = tmp_path / "explicit.json"
    cfg.write_text(
        json.dumps(
            {
                "COMP_TEST_NUM": 120,
                "COMP_TEST_BOOL": True,
                "COMP_TEST_NESTED": {"a": 1},
                "COMP_TEST_NULL": None,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COMP_AGENT_CONFIG", str(cfg))

    config.resolve_config()

    assert os.environ["COMP_TEST_NUM"] == "120"
    assert os.environ["COMP_TEST_BOOL"] == "1"
    assert "COMP_TEST_NESTED" not in os.environ
    assert "COMP_TEST_NULL" not in os.environ


def test_report_never_contains_secret_values(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    secret = "sk-test-super-secret-value-12345"
    cfg = tmp_path / "explicit.json"
    cfg.write_text(json.dumps({"OPENAI_API_KEY": secret}), encoding="utf-8")
    monkeypatch.setenv("COMP_AGENT_CONFIG", str(cfg))

    report = config.resolve_config()

    assert os.environ["OPENAI_API_KEY"] == secret
    assert secret not in json.dumps(report)
    assert report["openai_key_present"] is True
    assert "OPENAI_API_KEY" in report["keys_set"]


def test_load_config_matches_resolve_config_shape(tmp_path):
    (tmp_path / ".env").write_text("COMP_TEST_SHAPE=1\n", encoding="utf-8")

    report = config.load_config()

    assert set(report) == {"layers", "keys_set", "share_reachable", "openai_key_present"}
    assert "COMP_TEST_SHAPE" in report["keys_set"]


def test_cli_load_dotenv_shim_default_path(tmp_path):
    (tmp_path / ".env").write_text("COMP_TEST_SHIM_DEFAULT=shim\n", encoding="utf-8")

    assert cli.load_dotenv() is None  # original returned None

    assert os.environ["COMP_TEST_SHIM_DEFAULT"] == "shim"


def test_cli_load_dotenv_shim_explicit_path(tmp_path):
    env_file = tmp_path / "custom.env"
    env_file.write_text("COMP_TEST_SHIM_EXPLICIT=shim-value\n", encoding="utf-8")

    cli.load_dotenv(env_file)

    assert os.environ["COMP_TEST_SHIM_EXPLICIT"] == "shim-value"


def test_cli_load_dotenv_shim_respects_existing_env(monkeypatch, tmp_path):
    monkeypatch.setenv("COMP_TEST_SHIM_EXISTING", "original")
    env_file = tmp_path / ".env"
    env_file.write_text("COMP_TEST_SHIM_EXISTING=overwritten\n", encoding="utf-8")

    cli.load_dotenv(env_file)

    assert os.environ["COMP_TEST_SHIM_EXISTING"] == "original"


def test_cli_load_dotenv_shim_missing_file_is_noop(tmp_path):
    cli.load_dotenv(tmp_path / "does-not-exist.env")  # must not raise
