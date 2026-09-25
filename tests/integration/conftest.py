"""The real server, a real browser, and the admin UI as the site under test.

Every other test in this repo wires the server against an unroutable Grid and
never opens a port. That is why the admin list could go blank in production
with the whole suite green. These start ``selenium-flow`` as a process, drive a
real browser through its MCP endpoint, and point that browser at the server's
own ``/admin`` page — so there is no demo site to maintain, and every flow here
tests the tools, the flow runner and the admin UI at once.

**Less is more.** A test here is a flow in ``flows/`` and nothing else: the one
test in ``test_admin_ui.py`` runs every file in that directory. Before adding a
flow, ask what a user would see break that no flow here already catches. If the
answer is "a detail of a behaviour already covered", it belongs in the unit
suite, which is fast and does not need a browser.

One exception, ``test_responsive.py``: whether the server keeps answering while
a browser is busy is not something a flow can see (§F4.19).

``PROFILE_DIR``, when set and ``py-spy`` is installed, records the server for the
whole run as a flamegraph there: MCP driving a browser and that browser driving
the admin page, at once, in one process.

Needs two settings, and skips without them:

- ``GRID_URL`` — a Selenium Grid or standalone node.
- ``ADMIN_ORIGIN`` — where *the browser* reaches this server. Not localhost: the
  browser runs in another container or pod. CI uses ``host.docker.internal``;
  in a cluster it is this pod's IP.

``REDIS_URL`` is passed through when set, because production runs Redis and the
blank list was a Redis-only fault.
"""

import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

GRID_URL = os.environ.get("GRID_URL", "")
ADMIN_ORIGIN = os.environ.get("ADMIN_ORIGIN", "").rstrip("/")
TOKEN = "integration-token"
# The session every flow runs in, and the name it must find on the admin page.
SESSION = "integration"
FLOWS = Path(__file__).parent / "flows"

def pytest_collection_modifyitems(items):
    if GRID_URL and ADMIN_ORIGIN:
        return
    skip = pytest.mark.skip(reason="needs GRID_URL and ADMIN_ORIGIN")
    for item in items:
        if Path(item.fspath).is_relative_to(Path(__file__).parent):
            item.add_marker(skip)


def _wait_until_serving(url: str, process: subprocess.Popen, log: Path) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"the server exited:\n{log.read_text()}")
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except urllib.error.HTTPError:
            return  # answering at all is serving; /health says 503 on a cold Grid
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"the server never answered:\n{log.read_text()}")


def _profiled(command: list[str]) -> list[str]:
    """``command`` under ``py-spy record`` when PROFILE_DIR asks for it.

    Its own child, so no ptrace privilege is needed; ``--nonblocking`` so a
    sample never pauses the server that ``test_responsive`` is timing.
    """
    directory = os.environ.get("PROFILE_DIR")
    spy = shutil.which("py-spy") if directory else None
    if not spy:
        return command
    Path(directory).mkdir(parents=True, exist_ok=True)
    svg = str(Path(directory, "server.svg"))
    return [spy, "record", "--nonblocking", "--threads", "--rate", "100",
            "--format", "flamegraph", "-o", svg, "--", *command]


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    """``selenium-flow`` on ADMIN_ORIGIN's port, with the flows in the shared
    library and the token as a secret leashed to the admin origin."""
    root = tmp_path_factory.mktemp("server")
    shutil.copytree(FLOWS, root / "data" / "global" / "flows")
    secret = root / "secrets" / "admin"
    secret.mkdir(parents=True)
    (secret / "token").write_text(TOKEN)
    (secret / "_allowed_urls").write_text(ADMIN_ORIGIN)

    port = urlsplit(ADMIN_ORIGIN).port or 80
    env = {
        **os.environ,
        "GRID_URL": GRID_URL,
        "MCP_AUTH_TOKEN": TOKEN,
        "FLOW_DATA_DIR": str(root / "data"),
        "SECRETS_DIRS": str(root / "secrets"),
        "HOST": "0.0.0.0",
        "PORT": str(port),
    }
    log = root / "server.log"
    with log.open("w") as out:
        process = subprocess.Popen(
            _profiled([sys.executable, "-m", "kubed.selenium_flow"]),
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    local = f"http://127.0.0.1:{port}"
    try:
        _wait_until_serving(f"{local}/health", process, log)
        yield local
    finally:
        # SIGINT to the group: the server shuts down the way Ctrl-C does, and
        # py-spy, when it is there, stops and writes its profile.
        os.killpg(process.pid, signal.SIGINT)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        # The server's own log is the first thing a failure here needs.
        sys.stdout.write(log.read_text())


@pytest.fixture
async def browser(server):
    """An MCP client holding a named session with a fresh browser.

    Fresh per test, because the admin page keeps its token in sessionStorage and
    a flow that signs in must find the sign-in form."""
    transport = StreamableHttpTransport(
        f"{server}/mcp",
        headers={"Authorization": f"Bearer {TOKEN}", "X-Session-Key": SESSION},
    )
    async with Client(transport) as client:
        await client.call_tool("open_session", {"width": 1280, "height": 900})
        try:
            yield client
        finally:
            await client.call_tool("end_browser", {}, raise_on_error=False)
