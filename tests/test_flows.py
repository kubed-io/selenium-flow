"""The flow store: naming, session resolution, and reading and writing YAML.

Storage only. Nothing here knows what a step is or how to run one — that is E2
and E3. What is asserted is the part that is expensive to get wrong later: which
session a caller's flows belong to, and that a name from a URL cannot become a
path outside the data directory.
"""

import pytest
import yaml

from kubed.selenium_flow import flows
from kubed.selenium_flow.flows import (
    GLOBAL_SESSION,
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
        CallerKey("stdio", "stdio"),
    ],
)
def test_everything_unnamed_shares_the_global_session(key):
    """A transport key is new on every reconnect, so a directory per key would
    bury the disk in folders whose flows nobody could reach again."""
    assert session_for(key) == GLOBAL_SESSION


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
        "steps": [{"tool": "write", "params": {"xpath": "//input", "text": "x"}}],
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
    # The count, not the content.
    assert summary["steps"] == 2


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
