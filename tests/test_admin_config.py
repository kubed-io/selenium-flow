"""The admin's door into a workspace's config: read, write and remove it.

Only the admin writes the config, so these routes are behind the Admin door and
nothing else in the server writes the file. A refused file stays readable and
editable here, since this is how it gets fixed.
"""

from urllib.parse import quote

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow.names import CONFIG_FILE

from .conftest import TOKEN

pytestmark = pytest.mark.unit

KEY = "desktop"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
VALID = "# offered here\nflows:\n  global:\n  - shop,login\n"


def url(key: str = KEY) -> str:
    return f"/admin/workspaces/{quote(key, safe='')}/config"


@pytest.fixture
def client(data_server):
    return TestClient(data_server.mcp.http_app())


def test_every_route_is_behind_the_admin_door(client, tmp_path):
    assert client.get(url()).status_code == 401
    assert client.put(url(), json={"text": VALID}).status_code == 401
    assert client.delete(url()).status_code == 401
    assert not (tmp_path / "workspaces" / KEY / CONFIG_FILE).exists()


def test_a_workspace_with_no_file_reads_as_defaults(client):
    body = client.get(url(), headers=AUTH).json()
    assert body == {
        "text": None,
        "config": {"flows.global": {"value": None, "source": "default"}},
        "error": None,
        "kept": False,
    }


def test_put_writes_the_text_as_given_and_answers_what_it_now_is(client, tmp_path):
    response = client.put(url(), json={"text": VALID}, headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "text": VALID,
        "config": {"flows.global": {"value": ["shop,login"], "source": "workspace"}},
        "error": None,
        "kept": True,
    }
    assert (tmp_path / "workspaces" / KEY / CONFIG_FILE).read_text() == VALID
    assert client.get(url(), headers=AUTH).json()["text"] == VALID


@pytest.mark.parametrize(
    "body", [{"text": "colour: blue\n"}, {"text": "flows: [oops\n"}, {"text": 3}, {}]
)
def test_put_refuses_what_does_not_validate_and_keeps_the_file(client, tmp_path, body):
    client.put(url(), json={"text": VALID}, headers=AUTH)
    response = client.put(url(), json=body, headers=AUTH)
    assert response.status_code == 400
    assert response.json()["error"]
    assert (tmp_path / "workspaces" / KEY / CONFIG_FILE).read_text() == VALID


def test_the_refusal_names_the_reason(client):
    response = client.put(url(), json={"text": "colour: blue\n"}, headers=AUTH)
    assert "`colour`" in response.json()["error"]


def test_a_refused_file_on_disk_is_still_read_with_its_reason(client, tmp_path):
    (tmp_path / "workspaces" / KEY).mkdir(parents=True)
    (tmp_path / "workspaces" / KEY / CONFIG_FILE).write_text("colour: blue\n")
    body = client.get(url(), headers=AUTH).json()
    assert body["text"] == "colour: blue\n"
    assert "`colour`" in body["error"]
    assert body["kept"] is True
    assert body["config"]["flows.global"] == {"value": None, "source": "default"}


def test_an_admin_fixes_a_refused_file_by_saving_over_it(client, tmp_path):
    (tmp_path / "workspaces" / KEY).mkdir(parents=True)
    (tmp_path / "workspaces" / KEY / CONFIG_FILE).write_text("colour: blue\n")
    assert client.put(url(), json={"text": VALID}, headers=AUTH).json()["error"] is None


def test_delete_removes_the_file_and_the_workspace_expires_again(client, tmp_path):
    client.put(url(), json={"text": VALID}, headers=AUTH)
    response = client.delete(url(), headers=AUTH)
    assert response.status_code == 200
    assert response.json()["kept"] is False
    assert not (tmp_path / "workspaces" / KEY / CONFIG_FILE).exists()


def test_the_list_says_kept_as_soon_as_a_config_is_saved(client):
    client.put(url(), json={"text": VALID}, headers=AUTH)
    rows = client.get("/admin/workspaces", headers=AUTH).json()["workspaces"]
    assert [r["kept"] for r in rows if r["key"] == KEY] == [True]


def test_a_key_that_cannot_be_a_folder_is_refused(client):
    assert client.get(url("../etc"), headers=AUTH).status_code in (400, 404)
    assert client.put(url("bad name"), json={"text": VALID}, headers=AUTH).status_code == 400


def test_with_no_data_dir_every_route_is_a_conflict(server):
    client = TestClient(server.mcp.http_app(), headers=AUTH)
    for response in (
        client.get(url()),
        client.put(url(), json={"text": VALID}),
        client.delete(url()),
    ):
        assert response.status_code == 409
        assert response.json() == {
            "error": "this server keeps no workspace folders: data.dir is unset"
        }


def test_only_the_admin_route_reaches_the_config(data_server):
    """No Token-door route is about the config: the one route that names it is
    the admin's."""
    paths = [
        route.path
        for route in data_server.mcp._additional_http_routes
        if "config" in route.path.lower()
    ]
    assert paths == ["/admin/workspaces/{key}/config"]


async def test_no_tool_writes_the_config(data_server):
    """An agent's boundary it cannot move: no tool takes the config, and the
    tools that write files cannot reach the workspace's root."""
    tools = await data_server.mcp.list_tools()
    assert not [t.name for t in tools if "config" in t.name.lower()]


# ---- a save keeps the record now, a remove lets it expire now ----------------------


def test_a_save_keeps_an_idle_workspaces_record_at_once(client, data_server):
    """The record is not written again after the save, and still never expires."""
    from kubed.selenium_flow.workspace.store import Workspace

    data_server.store.set(KEY, Workspace(session_id="a"))
    client.put(url(), json={"text": VALID}, headers=AUTH)
    assert data_server.store._data[KEY][0] == float("inf")


def test_a_remove_sets_the_expiry_again_at_once(client, data_server):
    from kubed.selenium_flow.workspace.store import Workspace

    client.put(url(), json={"text": VALID}, headers=AUTH)
    data_server.store.set(KEY, Workspace(session_id="a"))
    client.delete(url(), headers=AUTH)
    assert data_server.store._data[KEY][0] < float("inf")


def test_a_save_or_remove_with_no_record_writes_none(client, data_server):
    client.put(url(), json={"text": VALID}, headers=AUTH)
    client.delete(url(), headers=AUTH)
    assert KEY not in data_server.store._data


def test_with_redis_a_save_persists_the_key_and_a_remove_expires_it(tmp_path):
    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.server import SeleniumMCP
    from kubed.selenium_flow.workspace.store import RedisStore, Workspace

    from .fakes import FakeRedis

    fake = FakeRedis()
    redis = RedisStore(fake, prefix="p:", ttl=60)
    server = SeleniumMCP(
        Settings(
            grid={"url": "http://grid.invalid:4444"},
            auth={"token": TOKEN},
            data={"dir": str(tmp_path)},
        ),
        store=redis,
    )
    redis.set(KEY, Workspace(session_id="a"))
    client = TestClient(server.mcp.http_app(), headers=AUTH)
    client.put(url(), json={"text": VALID})
    assert fake.expiries[f"p:{KEY}"] is None
    client.delete(url())
    assert fake.expiries[f"p:{KEY}"] == 60
    redis.delete(KEY)
    client.put(url(), json={"text": VALID})
    assert f"p:{KEY}" not in fake.data
