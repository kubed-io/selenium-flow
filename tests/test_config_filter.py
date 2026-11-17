"""A workspace's `flows.global`: which shared flows it is offered.

Rows of tags joined by commas; a `global` flow passes when it carries every
tag of any row. Set in `workspace.yaml`, read through the one hook every
surface asks (`FlowStore.offered`), so the listing, `flows_here`, reading and
running by name and the tags in use all follow it. The admin still sees every
`global` flow, each saying whether this workspace is offered it.
"""

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.flows import api as flowapi
from kubed.selenium_flow.names import CONFIG_FILE, GLOBAL_WORKSPACE
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import NAMED, OTHER, TOKEN, calling_as
from .test_flow_tags import AUTH, HERE, admin, call, current, flow, resource

pytestmark = pytest.mark.unit


def configure(server, text, name=NAMED):
    folder = server.flows.root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / CONFIG_FILE).write_text(text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    built = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"},
        auth={"token": TOKEN},
        data={"dir": str(tmp_path)},
    ))
    calling_as(monkeypatch, NAMED)
    return built


@pytest.fixture
def library(server):
    server.flows.save(GLOBAL_WORKSPACE, "shop-login", flow("shop", "login", paths=["/**"]))
    server.flows.save(GLOBAL_WORKSPACE, "shop-buy", flow("shop", paths=["/**"]))
    server.flows.save(GLOBAL_WORKSPACE, "nightly", flow("reports", paths=["/**"]))
    server.flows.save(GLOBAL_WORKSPACE, "plain", flow(paths=["/**"]))
    return server


async def listed(server):
    return [e["name"] for e in (await resource(server, flowapi.LIST_URI))["flows"]]


async def test_unset_offers_every_global_flow(library):
    assert await listed(library) == ["nightly", "plain", "shop-buy", "shop-login"]
    configure(library, "flows: {}\n")
    assert await listed(library) == ["nightly", "plain", "shop-buy", "shop-login"]


async def test_a_row_needs_every_tag_and_any_row_will_do(library):
    configure(library, "flows:\n  global:\n  - shop,login\n  - reports\n")
    assert await listed(library) == ["nightly", "shop-login"]


async def test_the_filter_reads_tags_as_a_saved_tag_is(library):
    configure(library, "flows:\n  global:\n  - ' Shop , LOGIN '\n")
    assert await listed(library) == ["shop-login"]


def test_flows_here_names_only_what_is_offered(library):
    configure(library, "flows:\n  global: [reports]\n")
    assert [h["name"] for h in library.flows.flows_here(NAMED, HERE)] == ["nightly"]


async def test_the_tags_in_use_count_only_what_is_offered(library):
    configure(library, "flows:\n  global: [reports]\n")
    assert (await current(library))["tags"] == {"reports": 1}


async def test_a_flow_not_offered_reads_and_runs_as_no_flow(library, monkeypatch):
    from mcp.shared.exceptions import MCPError

    configure(library, "flows:\n  global: [reports]\n")
    with pytest.raises(MCPError, match="no flow called 'shop-buy'"):
        await resource(library, "flow://flows/shop-buy")
    monkeypatch.setattr(library.workspaces, "resolved", lambda name: ("b-1", "chrome"))
    with pytest.raises(ValueError, match="no flow called 'shop-buy'"):
        await call(library, flowapi.RUN_TOOL, name="shop-buy")


async def test_the_workspaces_own_flows_are_never_filtered(library):
    library.flows.save(NAMED, "mine", flow("elsewhere"))
    configure(library, "flows:\n  global: [reports]\n")
    assert await listed(library) == ["mine", "nightly"]


async def test_another_workspace_is_offered_by_its_own_config(library, monkeypatch):
    configure(library, "flows:\n  global: [reports]\n")
    calling_as(monkeypatch, OTHER)
    assert await listed(library) == ["nightly", "plain", "shop-buy", "shop-login"]


async def test_a_saved_change_applies_from_the_next_call(library):
    configure(library, "flows:\n  global: [reports]\n")
    assert await listed(library) == ["nightly"]
    client = TestClient(library.mcp.http_app(), headers=AUTH)
    saved = client.put(
        f"/admin/workspaces/{NAMED}/config", json={"text": "flows:\n  global: [shop]\n"}
    )
    assert saved.status_code == 200
    assert await listed(library) == ["shop-buy", "shop-login"]


def test_a_refused_file_offers_nothing(library):
    """The calls are refused at the edge; the hook below them fails closed too."""
    configure(library, "flows:\n  global: ['Not A Tag!']\n")
    assert library.flows.flows_here(NAMED, HERE) == []


def test_the_admin_sees_every_global_flow_and_which_are_offered(library):
    configure(library, "flows:\n  global: [reports]\n")
    client = TestClient(library.mcp.http_app(), headers=AUTH)
    entries = {e["name"]: e for e in client.get(admin()).json()["flows"]}
    assert sorted(entries) == ["nightly", "plain", "shop-buy", "shop-login"]
    assert {n: e["offered"] for n, e in entries.items()} == {
        "nightly": True, "plain": False, "shop-buy": False, "shop-login": False,
    }
    assert client.get(admin("shop-buy")).status_code == 200


def test_the_admin_listing_marks_nothing_when_unset(library):
    client = TestClient(library.mcp.http_app(), headers=AUTH)
    assert all(e["offered"] for e in client.get(admin()).json()["flows"])
