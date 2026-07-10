"""Smoke tests for the desktop launcher (WS-2).

No network and no OpenAI key required: nothing here calls ``load_config``
(which probes the office share) or the OpenAI API. The server test binds an
ephemeral port on 127.0.0.1 only.
"""

from __future__ import annotations

import socket
import urllib.request

from comp_agent import app


def test_app_module_imports_and_exposes_entry_points() -> None:
    assert callable(app.main)
    assert callable(app.pick_port)
    assert callable(app.start_server_thread)


def test_version_flag_prints_version_and_exits(capsys) -> None:
    exit_code = app.main(["--version"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Comp Agent" in out
    assert app.get_version() in out


def test_pick_port_returns_usable_free_port() -> None:
    port = app.pick_port()
    assert 0 < port < 65536
    # The returned port must actually be bindable.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((app.DEFAULT_HOST, port))


def test_pick_port_falls_back_when_preferred_port_is_taken() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.bind((app.DEFAULT_HOST, 0))
        blocker.listen(1)
        taken = blocker.getsockname()[1]
        port = app.pick_port(preferred=taken)
        assert port != taken
        assert 0 < port < 65536


def test_server_starts_and_serves_index(tmp_path) -> None:
    port = app.pick_port(preferred=0)  # ephemeral: never collides with a real run
    thread = app.start_server_thread(app.DEFAULT_HOST, port, output_root=str(tmp_path / "projects_ui"))
    assert app.wait_until_serving(app.DEFAULT_HOST, port, timeout=10.0)

    # Loopback via a proxy-bypassing opener — a corporate registry proxy that
    # doesn't bypass 127.0.0.1 must not make this test (or the app) fail.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://{app.DEFAULT_HOST}:{port}/", timeout=5) as response:
        assert response.status == 200
        body = response.read().decode("utf-8")
    assert "Comp Study Deck Builder" in body

    # A second launch must detect this instance instead of racing for a port.
    assert app.find_running_instance(app.DEFAULT_HOST, port) is True

    # Shutdown model: the server runs on a daemon thread, so it dies with the
    # process — the launcher's Ctrl+C / close-the-window behavior.
    assert thread.daemon
    assert thread.is_alive()


def test_find_running_instance_false_when_nothing_is_listening() -> None:
    port = app.pick_port(preferred=0)  # free port, nothing bound to it
    assert app.find_running_instance(app.DEFAULT_HOST, port) is False
