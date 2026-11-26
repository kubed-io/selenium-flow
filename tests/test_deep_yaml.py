"""YAML nested deep enough to crash libyaml is refused, never a crash.

Each case runs in its own interpreter, so a regression is a failed test, not a
killed test run. The admin routes validate in a worker thread, as here.
"""

import json
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.unit

DEPTH = 50_000

SERVE = textwrap.dedent(
    """
    import json, sys
    from starlette.testclient import TestClient
    from kubed.selenium_flow.server import SeleniumMCP

    path, field, text = sys.argv[1], sys.argv[2], sys.stdin.read()
    server = SeleniumMCP({
        "grid": {"url": "http://grid.invalid:4444"},
        "auth": {"token": "t"},
        "data": {"dir": sys.argv[3]},
    })
    client = TestClient(server.mcp.server.http_app(), headers={"Authorization": "Bearer t"})
    if field:
        response = client.put(path, json={field: text})
    else:
        response = client.get(path)
    print(json.dumps([response.status_code, response.json()]))
    """
)


def put(tmp_path, path: str, field: str, text: str) -> tuple[int, dict]:
    """PUT ``{field: text}`` to ``path``, or GET it when ``field`` is empty."""
    done = subprocess.run(
        [sys.executable, "-c", SERVE, path, field, str(tmp_path)],
        input=text,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, (done.returncode, done.stderr[-2000:])
    status, body = json.loads(done.stdout.strip().splitlines()[-1])
    return status, body


def nested(prefix: str) -> str:
    return prefix + "[" * DEPTH + "]" * DEPTH + "\n"


def test_a_deeply_nested_config_is_refused(tmp_path):
    status, body = put(
        tmp_path, "/admin/workspaces/desktop/config", "text", nested("flows: ")
    )
    assert status == 400
    assert "nests too deeply" in body["error"]
    assert not (tmp_path / "workspaces" / "desktop").exists() or not any(
        (tmp_path / "workspaces" / "desktop").iterdir()
    )


def test_a_deeply_nested_flow_is_refused(tmp_path):
    status, body = put(
        tmp_path,
        "/admin/workspaces/desktop/flows/deep",
        "yaml",
        nested("name: deep\nsteps: "),
    )
    assert status == 400
    assert "nests too deeply" in body["error"]


def test_a_deeply_nested_config_placed_by_hand_is_read_as_refused(tmp_path):
    folder = tmp_path / "workspaces" / "desktop"
    folder.mkdir(parents=True)
    (folder / "workspace.yaml").write_text(nested("flows: "))
    status, body = put(tmp_path, "/admin/workspaces/desktop/config", "", "")
    assert status == 200
    assert "nests too deeply" in body["error"]


def _placed(tmp_path, text: str) -> None:
    """A deep flow and a plain one, written straight into the library."""
    flows = tmp_path / "workspaces" / "desktop" / "flows"
    flows.mkdir(parents=True)
    (flows / "deep.yaml").write_text(text)
    (flows / "plain.yaml").write_text(
        "name: plain\nsteps:\n- tool: navigate\n  args:\n    url: https://example.com/\n"
    )


@pytest.mark.parametrize(
    "text",
    [nested("name: deep\nsteps: "), "name: deep\nsteps:\n" + "- " * DEPTH + "x\n"],
    ids=["brackets", "block"],
)
def test_a_deeply_nested_flow_placed_by_hand_is_unreadable_not_a_crash(tmp_path, text):
    _placed(tmp_path, text)
    status, body = put(tmp_path, "/admin/workspaces/desktop/flows", "", "")
    assert status == 200
    steps = {flow["name"]: flow["step_count"] for flow in body["flows"]}
    # Listed as any unreadable file is: by name, with nothing read from it.
    assert steps == {"plain": 1, "deep": 0}


def test_tagging_a_deeply_nested_flow_placed_by_hand_is_refused(tmp_path):
    _placed(tmp_path, nested("name: deep\nsteps: "))
    done = subprocess.run(
        [sys.executable, "-c", SERVE.replace("client.put(path, json={field: text})",
                                             "client.put(path, json={field: json.loads(text)})"),
         "/admin/workspaces/desktop/flows/deep/tags", "tags", str(tmp_path)],
        input='["smoke"]', capture_output=True, text=True, timeout=120,
    )
    assert done.returncode == 0, (done.returncode, done.stderr[-2000:])
    status, _ = json.loads(done.stdout.strip().splitlines()[-1])
    assert status in (400, 404)
