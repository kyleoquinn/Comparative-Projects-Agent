"""Desktop launcher for the Comp Agent local UI (WS-2).

This is the entry point for the packaged (PyInstaller) build and for the
``comp-agent-app`` console script. It:

1. resolves config/secrets via :mod:`comp_agent.config` (layered lookup),
2. picks a port — 8765 preferred, an OS-assigned free port when that is taken,
3. starts the existing stdlib UI server (``comp_agent.ui.run_server``) on a
   daemon thread — the server itself is untouched,
4. opens the default browser at the local URL, and
5. keeps the process alive with a clear console message until Ctrl+C.

Flags: ``--version`` (print version and exit), ``--no-browser`` (skip the
browser auto-open; useful for smoke tests and headless QA).

No new dependencies, no changes to the stage or HTTP contracts.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser

from comp_agent.config import load_config
from comp_agent.ui import DEFAULT_OUTPUT_ROOT, run_server

DEFAULT_HOST = "127.0.0.1"
PREFERRED_PORT = 8765

# Fallback when package metadata is unavailable (e.g. a frozen build whose
# dist-info was not bundled). Keep in sync with pyproject.toml.
_FALLBACK_VERSION = "0.1.0"


def get_version() -> str:
    """Return the installed comp-agent version, or the baked-in fallback."""
    try:
        from importlib.metadata import version

        return version("comp-agent")
    except Exception:
        return _FALLBACK_VERSION


def pick_port(preferred: int = PREFERRED_PORT, host: str = DEFAULT_HOST) -> int:
    """Return a bindable local TCP port.

    Tries ``preferred`` first; if the bind fails (port already in use), falls
    back to an OS-assigned free port. The probe socket deliberately does NOT
    set ``SO_REUSEADDR`` — on Windows that flag would let the probe "succeed"
    on a port another process is already serving.
    """
    for candidate in (preferred, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind((host, candidate))
                return probe.getsockname()[1]
        except OSError:
            continue
    raise OSError(f"Could not find a free TCP port on {host}.")


def start_server_thread(host: str, port: int, output_root: str = DEFAULT_OUTPUT_ROOT) -> threading.Thread:
    """Start ``ui.run_server`` on a daemon thread and return the thread.

    ``run_server`` blocks in ``serve_forever`` and never returns, so it runs on
    a daemon thread; the whole server dies with the process (Ctrl+C or window
    close), which is the intended single-user desktop shutdown model.
    """
    thread = threading.Thread(
        target=run_server,
        kwargs={"host": host, "port": port, "output_root": output_root},
        name="comp-agent-ui-server",
        daemon=True,
    )
    thread.start()
    return thread


def wait_until_serving(host: str, port: int, timeout: float = 15.0) -> bool:
    """Poll ``http://host:port/`` until it answers 200, or the timeout passes."""
    from urllib.request import urlopen

    url = f"http://{host}:{port}/"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1.0) as response:
                if response.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.1)
    return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="comp-agent-app",
        description="Launch the Comp Agent desktop UI (local server + browser).",
    )
    parser.add_argument("--version", action="store_true", help="Print the app version and exit.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the web browser automatically.")
    parser.add_argument("--port", type=int, default=PREFERRED_PORT, help="Preferred port (falls back to a free port if taken).")
    parser.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT, help="Default project workspace root (the UI still requires an explicit output folder).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.version:
        print(f"Comp Agent {get_version()}")
        return 0

    print(f"Comp Agent {get_version()} — starting up...")

    # Layered config resolution (WS-1): process env wins, then COMP_AGENT_CONFIG,
    # app-adjacent files, the shared network config, and the repo-local .env.
    # The report contains key NAMES only — never secret values.
    report = load_config()
    if report.get("openai_key_present"):
        print("OpenAI key: found.")
    else:
        print("OpenAI key: NOT found — live search is unavailable; example data will be used.")
        if report.get("share_reachable") is False:
            print("(The shared network config was unreachable — are you connected to the VPN?)")

    port = pick_port(args.port)
    if port != args.port:
        print(f"Port {args.port} is in use; using port {port} instead.")
    url = f"http://{DEFAULT_HOST}:{port}/"

    thread = start_server_thread(DEFAULT_HOST, port, args.output_root)
    if not wait_until_serving(DEFAULT_HOST, port):
        print("The Comp Agent UI failed to start. Close this window and try again.", file=sys.stderr)
        return 1

    if not args.no_browser:
        webbrowser.open(url)

    print()
    print(f"Comp Agent is running at {url}")
    print("Leave this window open while you work.")
    print("Press Ctrl+C (or close this window) to stop.")
    try:
        while thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping Comp Agent. You can close this window.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
