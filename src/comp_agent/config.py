"""Layered config/secrets resolution for Comp Agent.

Precedence, FIRST HIT WINS per key:

1. Process environment variables — never overwritten (preserves current
   behavior; existing tests and consumers are untouched).
2. ``COMP_AGENT_CONFIG`` env var pointing at an explicit config file (JSON or
   ``.env``-style). When set, it also replaces the shared network default
   lookup (layer 4).
3. A config file next to the app: the executable directory when frozen under
   PyInstaller, otherwise the current working directory. Looks for
   ``comp_agent.config.json`` then ``comp_agent.env``.
4. The shared network default on the office share (UNC path, not the ``X:``
   drive letter): ``\\\\datafiles\\28_AI\\_AI AGENTS\\CompAgent\\`` —
   ``comp_agent.config.json`` then ``comp_agent.env``. Probed with a short
   timeout so an unreachable share can never hang startup.
5. The repo-local ``.env`` (the existing dev fallback).

Resolved values are written into ``os.environ`` ONLY for keys not already
set, so consumers (``openai_search.py``, ``stages.py``) keep reading env vars
exactly as before.

Security note: this module never logs, prints, or returns secret VALUES.
Reports contain key NAMES and source paths only.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import threading
from pathlib import Path

# Env var naming an explicit config file; overrides the shared network default.
CONFIG_PATH_ENV_VAR = "COMP_AGENT_CONFIG"

# Filenames looked up next to the app (exe dir when frozen, else CWD).
LOCAL_CONFIG_FILENAMES = ("comp_agent.config.json", "comp_agent.env")

# Shared office default. Use the UNC form, NOT the X: drive-letter mapping —
# drive letters differ per user/machine; the UNC path is the same for everyone.
SHARED_CONFIG_DIR = r"\\datafiles\28_AI\_AI AGENTS\CompAgent"
SHARED_CONFIG_FILES = (
    SHARED_CONFIG_DIR + r"\comp_agent.config.json",
    SHARED_CONFIG_DIR + r"\comp_agent.env",
)

# Probing an unreachable UNC share on Windows can block for a long time; the
# probe runs in a daemon thread and is abandoned after this many seconds.
NETWORK_PROBE_TIMEOUT_SECONDS = 2.5

__all__ = [
    "CONFIG_PATH_ENV_VAR",
    "LOCAL_CONFIG_FILENAMES",
    "SHARED_CONFIG_DIR",
    "SHARED_CONFIG_FILES",
    "NETWORK_PROBE_TIMEOUT_SECONDS",
    "load_config",
    "resolve_config",
    "parse_env_text",
    "parse_json_text",
]


def parse_env_text(text: str) -> dict[str, str]:
    """Parse ``.env``-style ``KEY=VALUE`` text into a dict.

    This is the parsing logic previously embedded in ``cli.load_dotenv``,
    moved here so both formats resolve through one code path. The first
    occurrence of a key wins, matching the original behavior.
    """
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in values:
            continue
        value = value.strip()
        if value:
            try:
                parts = shlex.split(value, posix=False)
            except ValueError:
                parts = []
            if len(parts) == 1:
                value = parts[0].strip("\"'")
            else:
                value = value.strip("\"'")
        values[key] = value
    return values


def parse_json_text(text: str) -> dict[str, str]:
    """Parse a flat JSON object of config keys into a dict of strings.

    Scalar values (str/int/float/bool) are coerced to env-var strings; nested
    objects, arrays, and nulls are skipped. Raises ``ValueError`` on malformed
    JSON or a non-object top level (callers tolerate and report it).
    """
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("config JSON must be a flat object of key/value pairs")
    values: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if isinstance(value, bool):
            values[key.strip()] = "1" if value else "0"
        elif isinstance(value, (str, int, float)):
            values[key.strip()] = str(value)
        # dict / list / None values are not representable as env vars: skip.
    return values


def _parse_config_text(text: str, source: str | Path) -> tuple[dict[str, str], str]:
    """Parse config file text, returning ``(values, format)``.

    ``.json`` files must be JSON; otherwise content sniffing tries JSON first
    when the text looks like an object, falling back to ``.env`` format.
    """
    suffix = Path(str(source)).suffix.lower()
    if suffix == ".json":
        return parse_json_text(text), "json"
    if text.lstrip("\ufeff \t\r\n").startswith("{"):
        try:
            return parse_json_text(text), "json"
        except ValueError:
            pass
    return parse_env_text(text), "env"


def _read_file(path: str | Path) -> str | None:
    """Read a config file's text; returns ``None`` on any failure. Never raises."""
    try:
        candidate = Path(path)
        if not candidate.is_file():
            return None
        return candidate.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    except Exception:  # pragma: no cover - absolute never-raise guarantee
        return None


def _read_file_with_timeout(path: str | Path, timeout: float) -> tuple[str | None, bool]:
    """Read a possibly-network path in a daemon thread; returns ``(text, timed_out)``.

    On timeout the daemon thread is abandoned (it cannot keep the process
    alive) and the layer is skipped, so an unreachable UNC share cannot hang
    startup.
    """
    result: list[str | None] = [None]

    def _target() -> None:
        result[0] = _read_file(path)

    worker = threading.Thread(target=_target, daemon=True, name="comp-agent-config-probe")
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        return None, True
    return result[0], False


def _apply_values(values: dict[str, str]) -> tuple[list[str], list[str]]:
    """Write values into ``os.environ`` for keys not already set.

    Returns ``(keys_set, keys_already_in_env)`` — key NAMES only, never values.
    """
    keys_set: list[str] = []
    keys_already: list[str] = []
    for key, value in values.items():
        if key in os.environ:
            keys_already.append(key)
            continue
        os.environ[key] = value
        keys_set.append(key)
    return keys_set, keys_already


def _load_layer(
    report: dict,
    layer: str,
    source: str | Path,
    *,
    timeout: float | None = None,
) -> dict:
    """Load one config file layer, apply it, and append a report entry."""
    entry: dict[str, object] = {"layer": layer, "source": str(source)}
    if timeout is None:
        text = _read_file(source)
        timed_out = False
    else:
        text, timed_out = _read_file_with_timeout(source, timeout)
    if timed_out:
        entry["status"] = "timeout"
    elif text is None:
        entry["status"] = "missing"
    else:
        try:
            values, fmt = _parse_config_text(text, source)
        except ValueError:
            entry["status"] = "parse-error"
        else:
            keys_set, keys_already = _apply_values(values)
            entry["status"] = "loaded"
            entry["format"] = fmt
            entry["keys_set"] = keys_set
            entry["keys_already_in_env"] = keys_already
            report["keys_set"].extend(keys_set)
    report["layers"].append(entry)
    return entry


def _app_dir() -> Path:
    """Directory to search for app-adjacent config files."""
    if getattr(sys, "frozen", False):  # PyInstaller bundle
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def resolve_config(dotenv_path: str | Path = ".env") -> dict:
    """Resolve layered config into ``os.environ`` and describe what happened.

    Existing process env vars always win — values are only written for keys
    not already set. Safe to call more than once (idempotent). Never raises
    on missing, malformed, or unreachable config paths.

    Returns a report dict that is safe to surface in the UI preflight — it
    contains layer/source/status info and the NAMES of keys set, never secret
    values::

        {
            "layers": [{"layer", "source", "status", ...}, ...],
            "keys_set": [...],          # all key names newly set this call
            "share_reachable": bool | None,  # None when the share layer was
                                             # skipped or not configured
            "openai_key_present": bool,  # OPENAI_API_KEY now in os.environ
        }
    """
    report: dict[str, object] = {"layers": [], "keys_set": [], "share_reachable": None}

    # Layer 2: explicit config file via COMP_AGENT_CONFIG.
    explicit = (os.environ.get(CONFIG_PATH_ENV_VAR) or "").strip()
    if explicit:
        # The explicit path may itself be a UNC path, so guard it too.
        _load_layer(report, "explicit", explicit, timeout=NETWORK_PROBE_TIMEOUT_SECONDS)
    else:
        report["layers"].append(
            {
                "layer": "explicit",
                "source": CONFIG_PATH_ENV_VAR,
                "status": "skipped",
                "reason": f"{CONFIG_PATH_ENV_VAR} not set",
            }
        )

    # Layer 3: config files next to the app (exe dir when frozen, else CWD).
    app_dir = _app_dir()
    for name in LOCAL_CONFIG_FILENAMES:
        _load_layer(report, "app_dir", app_dir / name)

    # Layer 4: shared network default — replaced entirely by COMP_AGENT_CONFIG.
    if explicit:
        for source in SHARED_CONFIG_FILES:
            report["layers"].append(
                {
                    "layer": "shared",
                    "source": str(source),
                    "status": "skipped",
                    "reason": f"overridden by {CONFIG_PATH_ENV_VAR}",
                }
            )
    else:
        share_reachable: bool | None = None
        shared_files = list(SHARED_CONFIG_FILES)
        for position, source in enumerate(shared_files):
            entry = _load_layer(
                report, "shared", source, timeout=NETWORK_PROBE_TIMEOUT_SECONDS
            )
            if entry["status"] == "timeout":
                share_reachable = False
                for remaining in shared_files[position + 1 :]:
                    report["layers"].append(
                        {
                            "layer": "shared",
                            "source": str(remaining),
                            "status": "skipped",
                            "reason": "share probe timed out",
                        }
                    )
                break
            share_reachable = True
        report["share_reachable"] = share_reachable

    # Layer 5: repo-local .env (dev fallback; original load_dotenv behavior).
    _load_layer(report, "dotenv", dotenv_path)

    report["openai_key_present"] = "OPENAI_API_KEY" in os.environ
    return report


def load_config(dotenv_path: str | Path = ".env") -> dict:
    """Load/refresh entry point (used by ``cli.load_dotenv``).

    Runs the full layered resolution and returns the same report as
    :func:`resolve_config`.
    """
    return resolve_config(dotenv_path=dotenv_path)
