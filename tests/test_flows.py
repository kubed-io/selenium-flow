"""The flow store: naming, session resolution, and reading and writing YAML.

Storage only. Nothing here knows what a step is or how to run one — that is E2
and E3. What is asserted is the part that is expensive to get wrong later: which
session a caller's flows belong to, and that a name from a URL cannot become a
path outside the data directory.
"""

import logging

import pytest
import yaml

from kubed.selenium_flow import flows
from kubed.selenium_flow.flows import (
    GLOBAL_SESSION,
    STDIO_SESSION,
    InvalidName,
    LocalFlowStore,
    session_for,
    valid_name,
)
from kubed.selenium_flow.sessions import CallerKey

pytestmark = pytest.mark.unit


@pytest.fixture
def store(tmp_path):
    return LocalFlowStore(tmp_path)


# ---- names -----------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["login", "weekly-report", "a", "A1", "flow.v2", "with_underscore", "9lives"]
)
def test_an_ordinary_name_is_accepted(name):
    assert valid_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "../etc",  # the obvious one
        "..",
        ".",
        ".hidden",  # a leading dot is how you hide a file, not name a flow
        "a/b",
        "a\\b",
        "",
        "   ",
        None,
        "with space",
        "sneaky/../../etc",
        "x" * 65,
    ],
)
def test_a_name_that_cannot_be_a_path_segment_is_refused(name):
    """Every one of these arrives from a caller. The session half comes off a
    URL query parameter that anyone who can reach the port can write."""
    with pytest.raises(InvalidName):
        valid_name(name)


def test_a_bad_name_is_refused_rather_than_slugged():
    """The failure mode of sanitising is a flow saved where nobody will look
    for it, silently. A 400 is fixed in one try."""
    with pytest.raises(InvalidName, match="not a usable"):
        valid_name("../etc/passwd", "flow name")


def test_the_error_names_which_kind_of_name_was_wrong():
    with pytest.raises(InvalidName, match="session name"):
        valid_name("../x", "session name")


# ---- which session owns a caller's flows ------------------------------------


def test_a_named_caller_gets_its_own_library():
    assert session_for(CallerKey("named:research-bot", "named")) == "research-bot"


@pytest.mark.parametrize(
    "key",
    [
        None,  # stateless: no key at all
        CallerKey("mcp:8f21c0aa-1b2c", "transport"),
    ],
)
def test_everything_unnamed_shares_the_global_session(key):
    """A transport key is new on every reconnect, so a directory per key would
    bury the disk in folders whose flows nobody could reach again.

    Both of these can name themselves — `?session=` on the URL, or the
    `X-Session-Key` header — which is what makes the read-only shared library a
    reasonable place to land them. Stdio cannot, and is below.
    """
    assert session_for(key) == GLOBAL_SESSION


def test_stdio_gets_a_library_of_its_own():
    """Stdio is one process serving one client, so a constant is right — and it
    has to be its *own* constant rather than `global`.

    A stdio client has no URL and no headers, so it cannot name itself. Landing
    it in the read-only shared library would leave it with no writable library
    at all and no way to obtain one: a refusal whose remedy cannot be performed.
    """
    assert session_for(CallerKey("stdio", "stdio")) == STDIO_SESSION
    assert STDIO_SESSION != GLOBAL_SESSION


def test_stdio_is_reserved_as_a_session_name_but_not_as_a_flow_name():
    """The reservation is about who may own that *library*. A flow called
    `stdio` is nobody's business but its author's, and `valid_name` still takes
    it — which is why the session rule is a separate function rather than a
    line inside that one."""
    from kubed.selenium_flow.flows import valid_session_name

    with pytest.raises(InvalidName, match="reserved"):
        valid_session_name(STDIO_SESSION)
    assert valid_name(STDIO_SESSION, "flow name") == STDIO_SESSION


def test_naming_yourself_global_is_legal_and_lands_in_the_same_place():
    assert session_for(CallerKey(f"named:{GLOBAL_SESSION}", "named")) == GLOBAL_SESSION


def test_a_session_key_that_cannot_be_a_directory_falls_back_rather_than_failing():
    """?session= keys the *browser* perfectly well whatever is in it — that is
    an opaque string in a store, not a path. Refusing it here would break a
    working session over a feature the caller is not using."""
    assert session_for(CallerKey("named:../etc", "named")) == GLOBAL_SESSION


# ---- reading and writing ----------------------------------------------------


def test_save_then_get_round_trips(store):
    document = {
        "description": "Log in",
        "steps": [{"tool": "write", "args": {"xpath": "//input", "text": "x"}}],
    }
    store.save("research-bot", "login", document)
    assert store.get("research-bot", "login") == {**document, "name": "login"}


def test_it_is_yaml_on_disk_so_a_person_can_edit_it(store, tmp_path):
    store.save("bot", "login", {"description": "Log in", "steps": []})
    path = tmp_path / "bot" / "flows" / "login.yaml"
    assert path.is_file()
    assert yaml.safe_load(path.read_text())["description"] == "Log in"


def test_saving_the_same_name_updates_rather_than_duplicating(store):
    store.save("bot", "login", {"description": "first", "steps": []})
    store.save("bot", "login", {"description": "second", "steps": []})
    assert store.names("bot") == ["login"]
    assert store.get("bot", "login")["description"] == "second"


def test_the_filename_is_the_flow_name_whatever_the_document_claims(store):
    """Otherwise save-then-get could hand back a flow under another name."""
    store.save("bot", "login", {"name": "something-else", "steps": []})
    assert store.get("bot", "login")["name"] == "login"


def test_a_missing_flow_is_none_not_an_error(store):
    assert store.get("bot", "nope") is None


def test_delete_reports_whether_there_was_anything_there(store):
    store.save("bot", "login", {"steps": []})
    assert store.delete("bot", "login") is True
    assert store.delete("bot", "login") is False
    assert store.get("bot", "login") is None


def test_one_session_cannot_see_another_s_flows(store):
    store.save("research-bot", "login", {"steps": []})
    assert store.names("form-filler") == []
    assert store.get("form-filler", "login") is None


def test_sessions_are_listed_for_the_admin_view(store):
    store.save("research-bot", "a", {"steps": []})
    store.save("form-filler", "b", {"steps": []})
    assert store.sessions() == ["form-filler", "research-bot"]


def test_nothing_is_created_until_something_is_written(tmp_path):
    """A read must not leave a directory behind for every name asked about."""
    store = LocalFlowStore(tmp_path / "data")
    assert store.sessions() == []
    assert store.names("bot") == []
    assert not (tmp_path / "data" / "bot").exists()


# ---- the listing stays affordable ------------------------------------------


def test_a_listing_carries_what_you_choose_by_and_not_the_steps(store):
    """`summaries` is what a WebDAV backend has to be able to afford: a caller
    picks a flow by name and description, and the steps are the bulk."""
    store.save(
        "bot",
        "login",
        {
            "description": "Log in",
            "parameters": {"type": "object", "properties": {"email": {}}},
            "steps": [{"tool": "navigate"}, {"tool": "write"}],
        },
    )
    (summary,) = store.summaries("bot")
    assert summary["name"] == "login"
    assert summary["description"] == "Log in"
    assert summary["parameters"]["properties"] == {"email": {}}
    # The count, not the content — and named so, because the document itself
    # carries a list under `steps`.
    assert summary["step_count"] == 2
    assert "steps" not in summary


# ---- damage a person can do by hand ----------------------------------------


def test_a_hand_broken_flow_reads_as_missing_rather_than_failing(store, tmp_path):
    """These are edited by hand and in the admin UI. One unparseable file must
    not take out every listing that walks past it."""
    store.save("bot", "good", {"steps": []})
    broken = tmp_path / "bot" / "flows" / "broken.yaml"
    broken.write_text("steps: [unclosed\n")
    assert store.get("bot", "broken") is None
    assert [s["name"] for s in store.summaries("bot")] == ["broken", "good"]


def test_a_yaml_file_that_is_not_a_mapping_reads_as_missing(store, tmp_path):
    store.save("bot", "good", {"steps": []})
    (tmp_path / "bot" / "flows" / "list.yaml").write_text("- just\n- a list\n")
    assert store.get("bot", "list") is None


# ---- the store the environment asks for -------------------------------------


def test_flows_are_off_unless_a_directory_is_named():
    """Not a temp-directory fallback: the operator chooses where this lives."""
    assert flows.from_env({}) is None
    assert flows.from_env({"FLOW_DATA_DIR": "   "}) is None


def test_naming_a_directory_turns_them_on(tmp_path):
    store = flows.from_env({"FLOW_DATA_DIR": str(tmp_path)})
    assert store is not None
    assert store.kind == "local"


def test_a_traversing_session_name_cannot_escape_the_data_directory(store):
    for attempt in ("../escape", "..", "a/../../b"):
        with pytest.raises(InvalidName):
            store.save(attempt, "flow", {"steps": []})
        with pytest.raises(InvalidName):
            store.get(attempt, "flow")


def test_a_traversing_flow_name_cannot_escape_either(store):
    with pytest.raises(InvalidName):
        store.save("bot", "../../escape", {"steps": []})


# ---- what the review caught -------------------------------------------------


def test_a_symlink_cannot_redirect_a_session_out_of_the_data_directory(store, tmp_path):
    """The containment check used to stop at the session directory, so a link
    left at <session>/flows redirected every read and write under it while the
    boundary still looked guarded. resolve() follows links at every level."""
    outside = tmp_path.parent / "outside"
    outside.mkdir()
    session = tmp_path / "bot"
    session.mkdir()
    (session / "flows").symlink_to(outside, target_is_directory=True)

    with pytest.raises(InvalidName, match="does not resolve to itself"):
        store.save("bot", "escape", {"steps": []})
    with pytest.raises(InvalidName, match="does not resolve to itself"):
        store.get("bot", "escape")
    assert list(outside.iterdir()) == []


def test_a_symlinked_session_directory_is_refused_too(store, tmp_path):
    outside = tmp_path.parent / "elsewhere"
    outside.mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(InvalidName, match="does not resolve to itself"):
        store.save("linked", "flow", {"steps": []})


def test_a_file_of_invalid_utf8_reads_as_missing(store, tmp_path):
    """UnicodeDecodeError is a ValueError, not an OSError, so it was slipping
    past the corruption branch — one bad byte took out the whole listing."""
    store.save("bot", "good", {"steps": []})
    (tmp_path / "bot" / "flows" / "binary.yaml").write_bytes(b"\xff\xfe steps: []")
    assert store.get("bot", "binary") is None
    assert [s["name"] for s in store.summaries("bot")] == ["binary", "good"]


def test_surrounding_whitespace_is_trimmed_rather_than_refused():
    """Not slugging: these arrive from URL query parameters and hand-written
    JSON, where a trailing space is a typo. A name that is only whitespace still
    names nothing and is still refused."""
    assert valid_name(" bot ") == "bot"
    assert session_for(CallerKey("named: bot ", "named")) == "bot"
    with pytest.raises(InvalidName):
        valid_name("   ")


def test_a_blank_explicit_directory_means_off_just_as_a_blank_env_does(monkeypatch):
    """The CLI flag's default IS the env var, so an unnormalised explicit value
    was the path FLOW_DATA_DIR actually took — and "   " became a directory
    named three spaces while from_env called the same value off."""
    from kubed.selenium_flow.server import SeleniumMCP

    monkeypatch.delenv("FLOW_DATA_DIR", raising=False)
    assert SeleniumMCP(grid_url="http://grid.invalid:4444", flow_data_dir="   ").flows is None
    assert SeleniumMCP(grid_url="http://grid.invalid:4444", flow_data_dir=None).flows is None


def test_an_explicit_directory_is_used_and_trimmed(monkeypatch, tmp_path):
    from kubed.selenium_flow.server import SeleniumMCP

    monkeypatch.delenv("FLOW_DATA_DIR", raising=False)
    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444", flow_data_dir=f"  {tmp_path}  "
    )
    assert server.flows is not None
    server.flows.save("bot", "login", {"steps": []})
    assert (tmp_path / "bot" / "flows" / "login.yaml").is_file()


# ---- what the second review caught ------------------------------------------


def test_a_symlink_to_another_session_is_refused_even_though_it_stays_inside(
    store, tmp_path
):
    """"Inside the data directory" was too weak: bot/flows -> research-bot/flows
    satisfies it and still hands one session another's library."""
    victim = tmp_path / "research-bot" / "flows"
    victim.mkdir(parents=True)
    store.save("research-bot", "secret", {"steps": [], "description": "theirs"})

    attacker = tmp_path / "bot"
    attacker.mkdir()
    (attacker / "flows").symlink_to(victim, target_is_directory=True)

    with pytest.raises(InvalidName, match="does not resolve to itself"):
        store.get("bot", "secret")
    with pytest.raises(InvalidName, match="does not resolve to itself"):
        store.save("bot", "secret", {"steps": [], "description": "mine"})
    # Untouched.
    assert store.get("research-bot", "secret")["description"] == "theirs"


def test_the_root_itself_may_be_a_link_because_that_is_the_installer_s_business(
    tmp_path,
):
    """FLOW_DATA_DIR pointing at a mount is exactly the case §F1.12 leaves open,
    so only the parts we join on have to be honest."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    store = LocalFlowStore(link)
    store.save("bot", "login", {"steps": []})
    assert store.get("bot", "login")["name"] == "login"
    assert (real / "bot" / "flows" / "login.yaml").is_file()


@pytest.mark.parametrize("filename", [".hidden.yaml", "._login.yaml", "with space.yaml"])
def test_a_filename_this_store_would_refuse_is_skipped_not_raised(
    store, tmp_path, filename
):
    """A directory is not only written by us. A macOS ._ file on a network
    mount, or anything hand-made, must not take the listing down."""
    store.save("bot", "good", {"steps": []})
    (tmp_path / "bot" / "flows" / filename).write_text("steps: []\n")
    assert store.names("bot") == ["good"]
    assert [s["name"] for s in store.summaries("bot")] == ["good"]


def test_a_symlinked_flow_file_is_skipped_from_the_listing(store, tmp_path):
    store.save("bot", "good", {"steps": []})
    outside = tmp_path.parent / "target.yaml"
    outside.write_text("steps: []\n")
    (tmp_path / "bot" / "flows" / "linked.yaml").symlink_to(outside)
    assert store.names("bot") == ["good"]


@pytest.mark.parametrize("steps", [1, {}, {"a": 1}, "three", None])
def test_a_hand_typed_steps_field_cannot_abort_a_listing(store, tmp_path, steps):
    """`steps: 1` reached len() and raised; `steps: {a: 1}` reported a mapping's
    size as a step count. Either hid every other flow in the session."""
    store.save("bot", "good", {"steps": [{"tool": "navigate"}]})
    (tmp_path / "bot" / "flows" / "odd.yaml").write_text(
        __import__("yaml").safe_dump({"steps": steps})
    )
    summaries = {s["name"]: s["step_count"] for s in store.summaries("bot")}
    assert summaries == {"good": 1, "odd": 0}


def test_every_operation_reports_the_same_identifier(store):
    """`get("bot", " login ")` read login.yaml but reported the flow as
    " login ", which no listing would ever return."""
    store.save("bot", " login ", {"steps": []})
    assert store.names("bot") == ["login"]
    assert store.get("bot", " login ")["name"] == "login"
    assert store.get("bot", "login")["name"] == "login"


def test_the_name_written_into_the_file_is_the_one_lookups_use(store, tmp_path):
    store.save("bot", " login ", {"steps": []})
    on_disk = yaml.safe_load((tmp_path / "bot" / "flows" / "login.yaml").read_text())
    assert on_disk["name"] == "login"


# ---- what a broken document is allowed to say about itself -------------------

BAD_YAML = """
name: login
steps:
  - tool: write
    params: {text: hunter2-the-actual-password
"""


def test_a_yaml_complaint_never_quotes_the_line_it_choked_on():
    """PyYAML's own message embeds the offending source verbatim, and this text
    reaches the HTTP response and the server log — which outlives the request
    and is read by people who were never shown the document. Position and the
    parser's short problem locate the mistake and carry none of the line."""
    with pytest.raises(yaml.YAMLError) as exc:
        yaml.safe_load(BAD_YAML)
    said = flows.yaml_complaint(exc.value)
    assert "hunter2" not in said
    assert "hunter2" in str(exc.value), "otherwise this test proves nothing"
    assert "line 6" in said and "column" in said


def test_a_yaml_complaint_survives_an_error_carrying_no_position():
    """`yaml.YAMLError` is a base class and not every subclass marks a spot."""
    assert flows.yaml_complaint(yaml.YAMLError("boom")) == "it could not be parsed"


def test_a_file_that_will_not_parse_is_logged_without_its_contents(
    store, tmp_path, caplog
):
    """The store reads hand-edited files, so it hits the same disclosure the
    editor does — one rule, applied in both places."""
    (tmp_path / "bot" / "flows").mkdir(parents=True)
    (tmp_path / "bot" / "flows" / "broken.yaml").write_text(BAD_YAML)
    with caplog.at_level(logging.WARNING):
        assert store.get("bot", "broken") is None
    assert "hunter2" not in caplog.text
    assert "broken" in caplog.text
