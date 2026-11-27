"""SIGTERM, the way Kubernetes stops a pod: prompt, clean, and quiet.

The real server as a process, because signal handling is uvicorn's and
FastMCP's, not anything a TestClient runs. An admin page's event stream is held
open while the signal lands: that stream never ends on its own, and as a plain
StreamingResponse it held every rollout for the whole grace, was cancelled, and
left a traceback per open page in the log.

An MCP listen stream needs the same. Clients hold a 2026-07-28
`subscriptions/listen` open for minutes; nothing ended it when the stop
started, so uvicorn cancelled it after its grace, with an ERROR and a traceback
in the log and a cut stream for the client.
"""

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from contextlib import contextmanager
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


@contextmanager
def _serve(tmp_path):
    """The server as a process: yields it, its base URL and its log file, and
    kills it on the way out."""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    log = tmp_path / "server.log"
    env = {
        **os.environ,
        # Refused at once, so the stream's Grid poll never waits on a timeout.
        "GRID_URL": "http://127.0.0.1:9",
        "AUTH_TOKEN": TOKEN,
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
        yield server, base, log
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()


def _listen(base: str) -> http.client.HTTPResponse:
    """Open a 2026-07-28 `subscriptions/listen` stream and return the response,
    its connection still open."""
    version = "2026-07-28"
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "subscriptions/listen",
        "params": {
            "notifications": {"toolsListChanged": True},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": version,
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {"name": "test_shutdown", "version": "1"},
            },
        },
    }
    host, port = base.removeprefix("http://").split(":")
    connection = http.client.HTTPConnection(host, int(port), timeout=10)
    connection.request(
        "POST",
        "/mcp",
        json.dumps(body),
        {
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": version,
            "Mcp-Method": "subscriptions/listen",
        },
    )
    response = connection.getresponse()
    assert response.status == 200, response.read()
    assert response.getheader("Content-Type", "").startswith("text/event-stream")
    return response


def _stop(server) -> float:
    started = time.monotonic()
    server.send_signal(signal.SIGTERM)
    server.wait(timeout=30)
    return time.monotonic() - started


def test_sigterm_stops_the_server_promptly_with_an_event_stream_open(tmp_path):
    with _serve(tmp_path) as (server, base, log):
        events = json.load(_get(f"{base}/admin/workspaces"))["events_url"]
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

    output = log.read_text()
    assert took < 1.5, f"shutdown took {took:.2f}s:\n{output}"
    assert "Traceback" not in output and "ERROR" not in output, output
    assert "Application shutdown complete" in output, output


@pytest.mark.parametrize("case", ["unread", "after-the-ack", "beside-an-admin-page"])
def test_sigterm_ends_an_open_listen_stream_cleanly(tmp_path, case):
    with _serve(tmp_path) as (server, base, log):
        stream = _listen(base)
        raised = []
        reader = None
        if case != "unread":
            # The first frame is the subscription's acknowledgement.
            lines = [stream.readline().decode() for _ in range(3)]
            assert lines[0] == "event: message\r\n", lines
            assert lines[1].startswith("data: ") and (
                "notifications/subscriptions/acknowledged" in lines[1]
            ), lines
            assert lines[2] == "\r\n", lines
        if case == "beside-an-admin-page":
            events = json.load(_get(f"{base}/admin/workspaces"))["events_url"]
            page = _get(f"{base}{events}", timeout=10)
            first = page.readline().decode()
            assert first.startswith("data: "), f"the stream sent {first!r} first"
            threading.Thread(target=page.read, daemon=True).start()
        if case != "unread":

            def drain():
                try:
                    stream.read()
                except Exception as error:  # noqa: BLE001 - what the client saw
                    raised.append(error)

            reader = threading.Thread(target=drain, daemon=True)
            reader.start()

        took = _stop(server)

    output = log.read_text()
    assert took < 5, f"shutdown took {took:.2f}s:\n{output}"
    for line in ("Traceback", "ERROR", "Exception in ASGI", "Cancel "):
        assert line not in output, output
    assert "Application shutdown complete" in output, output
    if reader is not None:
        reader.join(5)
        assert not reader.is_alive(), "the listen stream was never ended"
        assert raised == [], raised
