"""SIGTERM, the way Kubernetes stops a pod: prompt, clean, and quiet.

The real server as a process, because signal handling is uvicorn's and
FastMCP's, not anything a TestClient runs. An admin page's event stream is held
open while the signal lands: that stream never ends on its own, and as a plain
StreamingResponse it held every rollout for FastMCP's whole 2s grace, was
cancelled, and left a traceback per open page in the log (§F4.19).
"""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

TOKEN = "shutdown-token"
REPO = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get(url: str, timeout: float = 5):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    return urllib.request.urlopen(request, timeout=timeout)


def test_sigterm_stops_the_server_promptly_with_an_event_stream_open(tmp_path):
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    log = tmp_path / "server.log"
    env = {
        **os.environ,
        # Refused at once, so the stream's Grid poll never waits on a timeout.
        "GRID_URL": "http://127.0.0.1:9",
        "MCP_AUTH_TOKEN": TOKEN,
        "HOST": "127.0.0.1",
        "PORT": str(port),
        "PYTHONPATH": os.pathsep.join([str(REPO), *sys.path]),
    }
    with log.open("w") as out:
        server = subprocess.Popen(
            # -S carried over, for a checkout run with site-packages off.
            [sys.executable, *(["-S"] if sys.flags.no_site else []),
             "-m", "kubed.selenium_flow"],
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                _get(f"{base}/health", timeout=1).read()
                break
            except OSError:
                if server.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(
                        f"the server never answered:\n{log.read_text()}"
                    ) from None
                time.sleep(0.2)

        events = json.load(_get(f"{base}/admin/sessions"))["events_url"]
        stream = _get(f"{base}{events}", timeout=10)
        first = stream.readline().decode()
        assert first.startswith("data: "), f"the stream sent {first!r} first"
        # Keep reading, as a page does, so the connection is open and in use
        # when the signal lands.
        threading.Thread(target=stream.read, daemon=True).start()

        started = time.monotonic()
        server.send_signal(signal.SIGTERM)
        server.wait(timeout=10)
        took = time.monotonic() - started
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()

    output = log.read_text()
    assert took < 1.5, f"shutdown took {took:.2f}s:\n{output}"
    assert "Traceback" not in output and "ERROR" not in output, output
    assert "Application shutdown complete" in output, output
