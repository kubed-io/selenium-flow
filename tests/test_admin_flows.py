"""The admin's flow API: the operator path §F1.2 reserves.

`/flows/*` is scoped to whoever is calling and refuses the shared library.
This surface addresses **any** session and is allowed into `global`, because a
person is present who can see what a change affects. That difference is the
whole point of these routes existing, so it is what is asserted hardest here.

The other thing under test is that the editor is honest about YAML: a person
wrote these documents, comments included, and a save that round-tripped through
a parsed dict would quietly throw their comments and ordering away.
"""

from urllib.parse import quote

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow.flows import GLOBAL_SESSION
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.store import SessionRecord

from .conftest import TOKEN

pytestmark = pytest.mark.unit

KEY = "named:desktop"
SESSION = "desktop"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# A comment and a deliberate key order, so "stored verbatim" is testable rather
# than asserted. A dict round-trip loses both.
YAML = """\
# the one that logs us in
name: login
description: sign in to the demo site
steps:
- tool: navigate
  args:
    url: https://example.test/login
"""


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.delenv("FLOW_DATA_DIR", raising=False)
    built = SeleniumMCP(
        grid_url="http://grid.invalid:4444",
        auth_token=TOKEN,
        flow_data_dir=str(tmp_path),
    )
    built.sessions.store.set(KEY, SessionRecord(session_id=""))
    return built


@pytest.fixture
def client(server):
    return TestClient(server.mcp.http_app())


def url(name: str = "", suffix: str = "") -> str:
    base = f"/admin/sessions/{quote(KEY, safe='')}/flows"
    return f"{base}/{quote(name, safe='')}{suffix}" if name else base


# ---- listing -----------------------------------------------------------------


def test_every_route_needs_the_token(client):
    assert client.get(url()).status_code == 401
    assert client.get(url("login")).status_code == 401
    assert client.put(url("login"), json={"yaml": YAML}).status_code == 401
    assert client.delete(url("login")).status_code == 401
    assert client.post(url("login", "/move"), json={"to": "x"}).status_code == 401


def test_the_listing_is_the_same_merge_the_agent_sees(client, server):
    """If the admin showed a different library from the one a run would use,
    every answer it gave about "which login will this session run" would be a
    guess. Each entry says which library it came from; the UI marks the shared
    ones with a globe."""
    server.flows.save(SESSION, "mine", {"steps": [], "description": "mine"})
    server.flows.save(GLOBAL_SESSION, "shared", {"steps": [], "description": "ours"})
    body = client.get(url(), headers=AUTH).json()
    assert body["enabled"] is True
    assert {f["name"]: f["shared"] for f in body["flows"]} == {
        "mine": False,
        "shared": True,
    }


def test_with_flows_off_the_panel_is_empty_rather_than_broken(tmp_path, monkeypatch):
    """A disabled capability should render as "nothing here", not as an error
    that blanks the section and says a 400."""
    monkeypatch.delenv("FLOW_DATA_DIR", raising=False)
    off = SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=TOKEN)
    off.sessions.store.set(KEY, SessionRecord(session_id=""))
    body = TestClient(off.mcp.http_app()).get(url(), headers=AUTH)
    assert body.status_code == 200
    assert body.json() == {
        "key": KEY,
        "session": SESSION,
        "enabled": False,
        "flows": [],
    }


def test_a_session_whose_name_is_not_a_directory_lists_nothing(client, server):
    """It has no library, and must not be shown the shared one as though it
    were its own — the same rule the file counts follow."""
    server.sessions.store.set("named:my bot", SessionRecord(session_id=""))
    server.flows.save(GLOBAL_SESSION, "shared", {"steps": []})
    body = client.get(
        f"/admin/sessions/{quote('named:my bot', safe='')}/flows", headers=AUTH
    ).json()
    assert body["enabled"] is False and body["flows"] == []


# ---- reading and rewriting ---------------------------------------------------


def test_a_flow_comes_back_as_the_yaml_on_disk(client):
    client.put(url("login"), json={"yaml": YAML}, headers=AUTH)
    body = client.get(url("login"), headers=AUTH).json()
    assert body["yaml"] == YAML, "the editor was handed something re-serialised"
    assert body["name"] == "login" and body["shared"] is False
    # And the parsed view too, because the panel lists steps without parsing.
    assert body["steps"][0]["tool"] == "navigate"


def test_a_save_keeps_the_comment_and_the_ordering(client, server):
    """The reason `read_text`/`write_text` exist. Round-tripping through a dict
    is invisible until someone opens the editor, saves, and finds the note they
    left themselves has gone."""
    assert client.put(url("login"), json={"yaml": YAML}, headers=AUTH).status_code == 200
    assert server.flows.read_text(SESSION, "login") == YAML
    assert "# the one that logs us in" in server.flows.read_text(SESSION, "login")


def test_a_document_that_would_not_run_is_refused(client, server):
    bad = "name: bad\nsteps:\n- tool: nope\n  args: {}\n"
    response = client.put(url("bad"), json={"yaml": bad}, headers=AUTH)
    assert response.status_code == 400
    assert "no tool called" in response.json()["error"]
    assert server.flows.get(SESSION, "bad") is None, "it was written anyway"


def test_text_that_is_not_yaml_is_refused_as_such(client):
    response = client.put(url("bad"), json={"yaml": "steps: [\n"}, headers=AUTH)
    assert response.status_code == 400
    assert "valid YAML" in response.json()["error"]


def test_yaml_that_is_not_a_mapping_is_refused(client):
    response = client.put(url("bad"), json={"yaml": "- one\n- two\n"}, headers=AUTH)
    assert response.status_code == 400
    assert "mapping" in response.json()["error"]


def test_an_empty_body_says_what_is_missing(client):
    response = client.put(url("bad"), json={}, headers=AUTH)
    assert response.status_code == 400
    assert "yaml is required" in response.json()["error"]


def test_the_document_cannot_rename_the_flow(client, server):
    """The file name is the identity — `LocalFlowStore.get` overwrites whatever
    the document claims, so an edited `name:` renames nothing.

    Without this the save reports success, the flow keeps the name it had, and
    the file is left asserting a different one: a lie told twice. Refusing is
    not a smaller feature than renaming, it is an honest one.
    """
    client.put(url("login"), json={"yaml": YAML}, headers=AUTH)
    renamed = YAML.replace("name: login", "name: something-else")
    response = client.put(url("login"), json={"yaml": renamed}, headers=AUTH)
    assert response.status_code == 400
    assert "cannot rename" in response.json()["error"]
    assert "login" in response.json()["error"], "it should say which name to put back"
    # And nothing was written: a refusal that half-applied would be worse than
    # the silent rename it replaced.
    assert "name: login" in server.flows.read_text(SESSION, "login")
    assert server.flows.get(SESSION, "something-else") is None


def test_a_document_that_does_not_name_itself_is_still_saveable(client, server):
    """`name` is not required — the file supplies it. Only a *contradicting*
    one is refused, or hand-writing a flow would mean repeating its name."""
    body = "steps:\n- tool: navigate\n  args: {url: https://example.test/}\n"
    assert client.put(url("login"), json={"yaml": body}, headers=AUTH).status_code == 200
    assert server.flows.get(SESSION, "login")["name"] == "login"


def test_saving_an_untouched_document_is_not_a_rename(client, server):
    """The overwhelmingly common edit: open, change a selector, save. The
    document still carries its own name and that must not read as a rename."""
    client.put(url("login"), json={"yaml": YAML}, headers=AUTH)
    edited = YAML.replace("https://example.test/login", "https://example.test/signin")
    assert client.put(url("login"), json={"yaml": edited}, headers=AUTH).status_code == 200
    assert "signin" in server.flows.read_text(SESSION, "login")


def test_editing_a_shared_flow_edits_the_shared_one(client, server):
    """Not a fork. An operator opening a global flow, changing a selector and
    saving means "fix the shared login" — silently writing a private copy would
    leave every other session still running the broken one."""
    server.flows.save(GLOBAL_SESSION, "login", {"steps": [], "description": "old"})
    assert client.put(url("login"), json={"yaml": YAML}, headers=AUTH).status_code == 200
    assert server.flows.get(SESSION, "login") is None, "it forked into the session"
    assert "sign in to the demo site" in server.flows.read_text(GLOBAL_SESSION, "login")


# ---- deleting ----------------------------------------------------------------


def test_deleting_removes_it_from_the_folder_it_lives_in(client, server):
    server.flows.save(SESSION, "login", {"steps": []})
    body = client.delete(url("login"), headers=AUTH).json()
    assert body == {"deleted": True, "session": SESSION, "name": "login"}
    assert server.flows.get(SESSION, "login") is None


def test_deleting_a_shared_flow_is_allowed_here(client, server):
    """The agent surface refuses this; the operator surface is where it is
    permitted, which is the entire difference between the two."""
    server.flows.save(GLOBAL_SESSION, "login", {"steps": []})
    body = client.delete(url("login"), headers=AUTH).json()
    assert body["deleted"] is True and body["session"] == GLOBAL_SESSION
    assert server.flows.get(GLOBAL_SESSION, "login") is None


# ---- moving ------------------------------------------------------------------


def test_a_flow_moves_to_the_shared_library(client, server):
    """Promotion is this verb with `global` as the target — there is no separate
    promote, because a flow lives in exactly one directory (§F1.2)."""
    server.flows.write_text(SESSION, "login", YAML)
    body = client.post(
        url("login", "/move"), json={"to": GLOBAL_SESSION}, headers=AUTH
    ).json()
    assert body == {
        "moved": True,
        "from": SESSION,
        "session": GLOBAL_SESSION,
        "name": "login",
    }
    assert server.flows.get(SESSION, "login") is None, "it was copied, not moved"
    assert server.flows.read_text(GLOBAL_SESSION, "login") == YAML


def test_a_shared_flow_is_claimed_by_the_same_verb(client, server):
    """The round trip, which is what makes the button reversible: To global on
    an own flow, To this session on a shared one."""
    server.flows.write_text(GLOBAL_SESSION, "login", YAML)
    body = client.post(
        url("login", "/move"), json={"to": SESSION}, headers=AUTH
    ).json()
    assert body["moved"] is True and body["from"] == GLOBAL_SESSION
    assert server.flows.get(GLOBAL_SESSION, "login") is None
    assert server.flows.read_text(SESSION, "login") == YAML


def test_moving_between_two_sessions_needs_no_new_mechanism(client, server):
    """Push to global from one, claim from the other. The design leans on this,
    so it is worth proving rather than assuming."""
    server.flows.write_text("other", "login", YAML)
    other = f"/admin/sessions/{quote('named:other', safe='')}/flows/login/move"
    server.sessions.store.set("named:other", SessionRecord(session_id=""))
    client.post(other, json={"to": GLOBAL_SESSION}, headers=AUTH)
    client.post(url("login", "/move"), json={"to": SESSION}, headers=AUTH)
    assert server.flows.read_text(SESSION, "login") == YAML
    assert server.flows.get("other", "login") is None


def test_moving_somewhere_it_already_is_changes_nothing(client, server):
    server.flows.write_text(SESSION, "login", YAML)
    body = client.post(
        url("login", "/move"), json={"to": SESSION}, headers=AUTH
    ).json()
    assert body["moved"] is False
    assert server.flows.read_text(SESSION, "login") == YAML


def test_moving_somewhere_unusable_is_refused(client, server):
    server.flows.write_text(SESSION, "login", YAML)
    response = client.post(
        url("login", "/move"), json={"to": "../etc"}, headers=AUTH
    )
    assert response.status_code == 400
    assert server.flows.read_text(SESSION, "login") == YAML, "it moved anyway"


def test_moving_a_flow_that_is_not_there_says_where_to_look(client):
    response = client.post(
        url("nope", "/move"), json={"to": GLOBAL_SESSION}, headers=AUTH
    )
    assert response.status_code == 400
    assert "no flow called" in response.json()["error"]
