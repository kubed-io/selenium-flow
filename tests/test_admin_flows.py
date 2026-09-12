"""The admin's flow API: the operator path §F1.2 reserves.

`/flows/*` is scoped to whoever is calling and refuses the shared library.
This surface addresses **any** session and is allowed into `global`, because a
person is present who can see what a change affects. That difference is the
whole point of these routes existing, so it is what is asserted hardest here.

The other thing under test is that the editor is honest about YAML: a person
wrote these documents, comments included, and a save that round-tripped through
a parsed dict would quietly throw their comments and ordering away.
"""

from unittest.mock import patch
from urllib.parse import quote

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow import flows
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


TAKES = """\
name: search
parameters:
  properties:
    term: {type: string, description: what to look for}
    lang: {type: string}
    unread: {type: string}
  required: [term]
steps:
- tool: navigate
  args:
    url: https://${lang}.example.test/
- tool: write
  id: search
  args:
    css: input
    text: ${term}
- tool: extract
  id: heading
  args:
    xpath: //h1[contains(., '${term}')]
"""


def test_a_flow_says_which_steps_read_each_parameter(client):
    """The panel shows a parameter's `used by` list, and working that out means
    applying the `${name}` rule — which is a parser, not a substring search, and
    which already exists exactly once in `flowdoc.references`. Doing it in the
    browser would be a second implementation of the rule that could disagree
    with the one that actually substitutes at run time."""
    client.put(url("search"), json={"yaml": TAKES}, headers=AUTH)
    uses = client.get(url("search"), headers=AUTH).json()["uses"]
    assert uses["term"] == [1, 2], "every step that reads it, in order"
    assert uses["lang"] == [0]


def test_a_parameter_nothing_reads_is_listed_as_reading_nothing(client):
    """Almost always a typo in a step, so the panel can say so. It has to be an
    empty list rather than a missing key: absent would be indistinguishable
    from a parameter the server failed to look at."""
    client.put(url("search"), json={"yaml": TAKES}, headers=AUTH)
    uses = client.get(url("search"), headers=AUTH).json()["uses"]
    assert uses["unread"] == []


def test_a_flow_that_takes_nothing_uses_nothing(client):
    client.put(url("login"), json={"yaml": YAML}, headers=AUTH)
    assert client.get(url("login"), headers=AUTH).json()["uses"] == {}


def test_an_undeclared_reference_is_not_invented_as_a_parameter(client, server):
    """Saving refuses `${nowhere}`, so this can only arrive as a file someone
    edited on disk — which is exactly the case the panel has to survive. The
    reference must not appear in Params as though it had been declared: that
    section lists the flow's interface, and a typo is not part of it."""
    server.flows.write_text(
        SESSION, "stray", "name: stray\nsteps:\n- tool: navigate\n  args:\n    url: ${nowhere}\n"
    )
    assert client.get(url("stray"), headers=AUTH).json()["uses"] == {}


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


# ---- what the page watches ---------------------------------------------------


def test_the_listing_carries_the_revision_it_was_built_from(client, server):
    """The page records this and repaints only when it changes, so a failed
    load records nothing and is retried on the next poll."""
    client.put(url("login"), json={"yaml": YAML}, headers=AUTH)
    body = client.get(url(), headers=AUTH).json()
    assert body["rev"]


def test_the_revision_changes_when_a_flow_is_edited_in_place(client, server):
    """The case a count cannot see, and the one the panel is for. The name and
    the step count are identical after an edit; only the document moved."""
    client.put(url("login"), json={"yaml": YAML}, headers=AUTH)
    before = client.get(url(), headers=AUTH).json()
    edited = YAML.replace("https://example.test/login", "https://example.test/signin")
    client.put(url("login"), json={"yaml": edited}, headers=AUTH)
    after = client.get(url(), headers=AUTH).json()

    assert after["count"] == before["count"], "the count is why a count is not enough"
    assert after["rev"] != before["rev"]


def test_the_revision_follows_the_shared_library_too(client, server):
    """The panel lists this session's flows AND the shared ones, so a change an
    operator makes to `global` in another tab has to reach this page."""
    before = client.get(url(), headers=AUTH).json()["rev"]
    server.flows.save(GLOBAL_SESSION, "cookie-banner", {"steps": []})
    assert client.get(url(), headers=AUTH).json()["rev"] != before


def test_a_store_with_no_revision_falls_back_to_its_count(client, server):
    """The docstring promises the panel "degrades to what the file list already
    does". Returning a constant instead would make the page's stamp constant and
    it would never repaint — the bug this mechanism exists to fix, reintroduced
    silently for any store that is not the local one.

    Patched on the CLASS, not by reassigning `server.flows`: `admin.register`
    captured the store object at construction, so swapping the attribute
    afterwards left the route using the real one and the test passed without
    ever reaching the branch it names.
    """
    server.flows.save(SESSION, "one", {"steps": []})
    with patch.object(
        flows.LocalFlowStore, "revision", side_effect=AttributeError("revision")
    ):
        one = client.get(url(), headers=AUTH).json()["rev"]
        server.flows.save(SESSION, "two", {"steps": []})
        two = client.get(url(), headers=AUTH).json()["rev"]

    # The count, twice — this session's and the shared library's — which is what
    # the fallback returns, and proof the branch was taken rather than the real
    # revision being read.
    assert one == "1+0"
    assert two == "2+0"

def test_the_shared_revision_is_read_once_per_payload(client, server, monkeypatch):
    """It is the same answer for every row, and inside the loop each heartbeat
    walked and stat-ed the whole shared library once per session — O(sessions x
    shared flows) on a two-second poll."""
    for n in range(3):
        server.sessions.store.set(f"named:s{n}", SessionRecord(session_id=""))
    seen = []
    real = server.flows.revision
    monkeypatch.setattr(
        server.flows, "revision", lambda s: (seen.append(s), real(s))[1], raising=False
    )
    client.get("/admin/sessions", headers=AUTH)
    assert seen.count(GLOBAL_SESSION) == 1, seen


def test_the_revision_is_read_before_the_listing(client, server, monkeypatch):
    """An edit landing between the two would otherwise pair the old summaries
    with the new revision — the page records that token, sees no change next
    poll, and keeps showing what it already had. This order costs one redundant
    refresh instead of suppressing a real one."""
    order = []
    real_rev, real_cat = server.flows.revision, server.flows.summaries
    monkeypatch.setattr(
        server.flows, "revision", lambda s: (order.append("rev"), real_rev(s))[1]
    )
    monkeypatch.setattr(
        server.flows, "summaries", lambda s: (order.append("list"), real_cat(s))[1]
    )
    client.get(url(), headers=AUTH)
    assert order and order[0] == "rev", order
