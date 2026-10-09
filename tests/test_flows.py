"""The flow store: naming, workspace resolution, and reading and writing YAML.

Storage only. Nothing here knows what a step is or how to run one — that is E2
and E3. What is asserted is the part that is expensive to get wrong later: which
workspace a caller's flows belong to, and that a name from a URL cannot become a
path outside the data directory.
"""

import logging
import os
import threading
import time

import pytest
import yaml

from kubed.selenium_flow.config import ConfigError, DataSettings
from kubed.selenium_flow.flows import library as flows
from kubed.selenium_flow.flows import store as flowstore
from kubed.selenium_flow.flows.store import LocalFlowStore
from kubed.selenium_flow.names import (
    GLOBAL_WORKSPACE,
    STDIO_WORKSPACE,
    InvalidName,
    library_of,
    valid_name,
)

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
    """Every one of these arrives from a caller. The workspace half comes off a
    URL query parameter that anyone who can reach the port can write."""
    with pytest.raises(InvalidName):
        valid_name(name)


def test_a_bad_name_is_refused_rather_than_slugged():
    """The failure mode of sanitising is a flow saved where nobody will look
    for it, silently. A 400 is fixed in one try."""
    with pytest.raises(InvalidName, match="not a usable"):
        valid_name("../etc/passwd", "flow name")


def test_the_error_names_which_kind_of_name_was_wrong():
    with pytest.raises(InvalidName, match="workspace name"):
        valid_name("../x", "workspace name")


# ---- which workspace owns a caller's flows ------------------------------------


def test_a_workspace_owns_the_library_of_its_own_name():
    """One answer now, where there were three. A workspace name IS a directory
    name — validated where it arrives (§F2.12) — so there is no longer a lenient
    resolver for browsers, a strict one for storage, and a third answering None
    for the admin list."""
    assert library_of("research-bot") == "research-bot"


def test_stdio_gets_a_library_of_its_own():
    """Stdio is one process serving one client, so a constant is right — and it
    has to be its *own* constant rather than `global`.

    A stdio client has no URL and no headers, so it cannot name itself. Landing
    it in the read-only shared library would leave it with no writable library
    at all and no way to obtain one: a refusal whose remedy cannot be performed.
    """
    assert library_of(STDIO_WORKSPACE) == STDIO_WORKSPACE
    assert STDIO_WORKSPACE != GLOBAL_WORKSPACE


def test_the_reserved_names_are_reserved_as_workspaces_but_not_as_flow_names():
    """The reservation is about who may own that *library*. A flow called
    `stdio` or `global` is nobody's business but its author's, and `valid_name`
    still takes it — which is why the workspace rule is a separate function rather
    than a line inside that one."""
    from kubed.selenium_flow.names import valid_workspace_name

    for reserved in (STDIO_WORKSPACE, GLOBAL_WORKSPACE):
        with pytest.raises(InvalidName, match="reserved"):
            valid_workspace_name(reserved)
        assert valid_name(reserved, "flow name") == reserved


# ---- reading and writing ----------------------------------------------------


def test_save_then_get_round_trips(store):
    document = {
        "description": "Log in",
        "steps": [{"tool": "write", "args": {"selector": {"xpath": "//input"}, "text": "x"}}],
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


def test_one_workspace_cannot_see_another_s_flows(store):
    store.save("research-bot", "login", {"steps": []})
    assert store.names("form-filler") == []
    assert store.get("form-filler", "login") is None


def test_workspaces_are_listed_for_the_admin_view(store):
    store.save("research-bot", "a", {"steps": []})
    store.save("form-filler", "b", {"steps": []})
    assert store.workspaces() == ["form-filler", "research-bot"]


def test_nothing_is_created_until_something_is_written(tmp_path):
    """A read must not leave a directory behind for every name asked about."""
    store = LocalFlowStore(tmp_path / "data")
    assert store.workspaces() == []
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


# ---- parsing, which every listing pays for per flow (§F4.19) -----------------


def test_a_hand_edit_is_read_at_once_not_from_a_cache(store, tmp_path):
    """Parsed flows are cached, and a cache that served the old flow after an
    edit would make the editor lie. Same length on purpose, so a key built from
    size or a coarse mtime would miss it."""
    store.save("bot", "login", {"description": "aaaa", "steps": []})
    assert store.get("bot", "login")["description"] == "aaaa"
    path = tmp_path / "bot" / "flows" / "login.yaml"
    path.write_text(path.read_text().replace("aaaa", "bbbb"))
    assert store.get("bot", "login")["description"] == "bbbb"


def test_what_get_returns_is_the_callers_to_change(store):
    """One parse is shared by every read of the same file, so handing out the
    cached object would let one caller's edit leak into the next read."""
    store.save("bot", "login", {"steps": [{"tool": "navigate", "args": {"url": "a"}}]})
    first = store.get("bot", "login")
    first["steps"][0]["args"]["url"] = "changed"
    first["steps"].append({"tool": "back"})
    assert store.get("bot", "login")["steps"] == [
        {"tool": "navigate", "args": {"url": "a"}}
    ]


def test_a_flow_is_parsed_once_however_often_it_is_read(store, monkeypatch):
    """The Secrets tab and every listing read every stored flow; parsing each
    one again per request was 1.5s for 26 flows in the pod."""
    store.save("bot", "once", {"description": "parsed-once-probe", "steps": []})
    calls = []
    real = flows.yaml.load
    monkeypatch.setattr(
        flows.yaml, "load", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    for _ in range(5):
        assert store.get("bot", "once")["description"] == "parsed-once-probe"
        store.summaries("bot")
    assert len(calls) == 1


def test_readers_arriving_together_share_one_parse(store, monkeypatch):
    """Routes run in a thread pool, so the page's first load can ask for the
    same cold flow several times at once; the others wait for the one parse."""
    store.save("bot", "herd", {"description": "stampede-probe", "steps": []})
    calls = []
    real = flows.yaml.load

    def slow(*a, **k):
        calls.append(1)
        time.sleep(0.2)
        return real(*a, **k)

    monkeypatch.setattr(flows.yaml, "load", slow)
    threads = [
        threading.Thread(target=store.get, args=("bot", "herd")) for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1


def test_a_flow_bigger_than_the_cache_is_read_but_never_kept(store, monkeypatch):
    """The cache is bounded by YAML source size, so one huge document cannot
    hold the pod's memory: it is still read, just parsed every time."""
    description = "y" * (flows.CACHE_BYTES + 1)
    store.save("bot", "huge", {"description": description, "steps": []})
    calls = []
    real = flows.yaml.load
    monkeypatch.setattr(
        flows.yaml, "load", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    for _ in range(2):
        assert store.get("bot", "huge")["description"] == description
    assert len(calls) == 2


def test_the_budget_counts_bytes_not_characters(store, monkeypatch):
    """Under the budget in characters, over it in UTF-8: "é" is two bytes, and
    a budget counted in characters would let such a flow take twice its share."""
    description = "é" * (flows.CACHE_BYTES // 2 + 1)
    assert len(description) < flows.CACHE_BYTES, "otherwise this proves nothing"
    store.save("bot", "accents", {"description": description, "steps": []})
    calls = []
    real = flows.yaml.load
    monkeypatch.setattr(
        flows.yaml, "load", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    for _ in range(2):
        assert store.get("bot", "accents")["description"] == description
    assert len(calls) == 2


def test_empty_documents_cannot_fill_the_cache_for_free():
    """An empty file is zero bytes of source and still a key and a dict; with
    no per-entry cost, a folder of them would grow the cache without bound."""
    for n in range(6000):
        flows.parse(f"# empty-probe {n}\n")
    assert len(flows._parsed.cache) <= flows.CACHE_BYTES // flows.ENTRY_BYTES


def test_flows_are_parsed_by_libyaml_when_the_wheel_has_it():
    """Ten times the pure-Python parser, and every platform we ship has it."""
    if not yaml.__with_libyaml__:
        pytest.skip("this PyYAML was built without libyaml")
    assert flows._LOADER is yaml.CSafeLoader


# ---- the store the environment asks for -------------------------------------


def test_flows_are_off_unless_a_directory_is_named():
    """Not a temp-directory fallback: the operator chooses where this lives."""
    assert flowstore.from_settings(DataSettings()) is None
    assert flowstore.from_settings(DataSettings(dir="   ")) is None


def test_naming_a_directory_turns_them_on(tmp_path):
    store = flowstore.from_settings(DataSettings(dir=str(tmp_path)))
    assert store is not None
    assert store.kind == "local"


def test_a_traversing_workspace_name_cannot_escape_the_data_directory(store):
    for attempt in ("../escape", "..", "a/../../b"):
        with pytest.raises(InvalidName):
            store.save(attempt, "flow", {"steps": []})
        with pytest.raises(InvalidName):
            store.get(attempt, "flow")


def test_a_traversing_flow_name_cannot_escape_either(store):
    with pytest.raises(InvalidName):
        store.save("bot", "../../escape", {"steps": []})


# ---- what the review caught -------------------------------------------------


def test_a_symlink_cannot_redirect_a_workspace_out_of_the_data_directory(store, tmp_path):
    """The containment check used to stop at the workspace directory, so a link
    left at <workspace>/flows redirected every read and write under it while the
    boundary still looked guarded. resolve() follows links at every level."""
    outside = tmp_path.parent / "outside"
    outside.mkdir()
    workspace = tmp_path / "bot"
    workspace.mkdir()
    (workspace / "flows").symlink_to(outside, target_is_directory=True)

    with pytest.raises(InvalidName, match="does not resolve to itself"):
        store.save("bot", "escape", {"steps": []})
    with pytest.raises(InvalidName, match="does not resolve to itself"):
        store.get("bot", "escape")
    assert list(outside.iterdir()) == []


def test_a_symlinked_workspace_directory_is_refused_too(
    store, tmp_path, tmp_path_factory
):
    # Its own directory: tmp_path.parent is shared by every test in the worker,
    # and another test that made "elsewhere" there turned this into an error.
    outside = tmp_path_factory.mktemp("elsewhere")
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
    from kubed.selenium_flow.names import valid_workspace_name

    assert valid_name(" bot ") == "bot"
    assert valid_workspace_name(" bot ") == "bot"
    with pytest.raises(InvalidName):
        valid_name("   ")


def test_a_blank_explicit_directory_means_off_just_as_a_blank_env_does():
    """`DataSettings` normalises "   " to None (see `_blank_is_off`), so a
    caller building `Settings` by hand cannot end up with a directory named
    three spaces."""
    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.server import SeleniumMCP

    grid = {"url": "http://grid.invalid:4444"}
    assert SeleniumMCP(Settings(grid=grid, data={"dir": "   "})).flows is None
    assert SeleniumMCP(Settings(grid=grid, data={"dir": None})).flows is None


def test_an_explicit_directory_is_used_and_trimmed(tmp_path):
    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.server import SeleniumMCP

    server = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"}, data={"dir": f"  {tmp_path}  "}
    ))
    assert server.flows is not None
    server.flows.save("bot", "login", {"steps": []})
    assert (tmp_path / "workspaces" / "bot" / "flows" / "login.yaml").is_file()


# ---- what the second review caught ------------------------------------------


def test_a_symlink_to_another_workspace_is_refused_even_though_it_stays_inside(
    store, tmp_path
):
    """"Inside the data directory" was too weak: bot/flows -> research-bot/flows
    satisfies it and still hands one workspace another's library."""
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
    """DATA_DIR pointing at a mount is exactly the case §F1.12 leaves open,
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


def test_an_edit_inside_one_clock_tick_still_moves_the_revision(store, tmp_path):
    """Two writes inside one tick share an mtime; the page would keep showing
    the first. Size and inode sit in the stamp beside it, the way git does."""
    store.save("bot", "login", {"description": "a", "steps": []})
    path = tmp_path / "bot" / "flows" / "login.yaml"
    before = path.stat()
    first = store.revision("bot")
    path.write_text(path.read_text().replace("a", "a longer one"))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert store.revision("bot") != first


def test_a_linked_kept_file_is_never_listed(store, tmp_path):
    """Files and Screenshots list a folder with no realpath per entry, so the
    listing itself has to refuse a link the store would refuse to read."""
    store.write_file("bot", "real.png", b"png")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"secret")
    (tmp_path / "bot" / "files" / "linked.png").symlink_to(outside)
    assert [f["name"] for f in store.files("bot")] == ["real.png"]


@pytest.mark.parametrize("swapped", ["files", "workspace"])
def test_a_directory_swapped_for_a_link_after_it_was_checked_is_refused(
    tmp_path, monkeypatch, swapped
):
    """Listings trust the directory `_resolved` returned rather than realpath
    each entry, so a link swapped in after that check must still be refused,
    whether it replaces the folder itself or a directory above it."""
    root = tmp_path.resolve()
    store = LocalFlowStore(root)
    store.write_file("other", "private.png", b"png")
    checked = root / "bot" / "files"
    if swapped == "files":
        (root / "bot").mkdir()
        checked.symlink_to(root / "other" / "files")
    else:
        (root / "bot").symlink_to(root / "other")
    monkeypatch.setattr(store, "_files_dir", lambda workspace, folder="files": checked)
    with pytest.raises(InvalidName):
        store.files("bot")


@pytest.mark.parametrize("kind", ["files", "flows"])
def test_without_a_descriptor_to_pin_each_entry_is_checked(
    tmp_path, monkeypatch, kind
):
    """Where `scandir` cannot take a descriptor, the directory is listed by
    path, so a swap mid-listing is caught per entry as it used to be — on the
    file itself, `login.yaml`, not on the flow name it is listed as."""
    root = tmp_path.resolve()
    store = LocalFlowStore(root)
    if kind == "files":
        store.write_file("bot", "real.png", b"png")
        entry, listing = root / "bot" / "files" / "real.png", store.files
    else:
        store.save("bot", "login", {"steps": []})
        entry, listing = root / "bot" / "flows" / "login.yaml", store.names
    real_resolve = type(entry).resolve

    def swapped(self, *args, **kwargs):
        # The directory itself still checks out; the entry under it does not,
        # as if the folder moved under a link after the directory check.
        if self == entry:
            return root / "elsewhere" / entry.name
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(os, "supports_fd", set())
    monkeypatch.setattr(type(entry), "resolve", swapped)
    with pytest.raises(InvalidName):
        listing("bot")


def test_a_symlinked_flow_file_is_skipped_from_the_listing(store, tmp_path):
    store.save("bot", "good", {"steps": []})
    outside = tmp_path.parent / "target.yaml"
    outside.write_text("steps: []\n")
    (tmp_path / "bot" / "flows" / "linked.yaml").symlink_to(outside)
    assert store.names("bot") == ["good"]


@pytest.mark.parametrize("steps", [1, {}, {"a": 1}, "three", None])
def test_a_hand_typed_steps_field_cannot_abort_a_listing(store, tmp_path, steps):
    """`steps: 1` reached len() and raised; `steps: {a: 1}` reported a mapping's
    size as a step count. Either hid every other flow in the workspace."""
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


def test_the_parser_the_store_uses_places_the_complaint_the_same_way():
    """libyaml words its problem differently but marks the same spot, so the
    editor's error points at the same line whichever parser read it."""
    with pytest.raises(yaml.YAMLError) as exc:
        flows.parse(BAD_YAML)
    said = flows.yaml_complaint(exc.value)
    assert "hunter2" not in said
    assert "line 6, column 1" in said


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


# ---- drag: the second address a schema cannot demand -------------------------


def _drag_problems(args):
    """What `save_flow` would say about one drag step."""
    from kubed.selenium_flow.flows import document as flowdoc

    schemas = {
        "drag": {
            "type": "object",
            "properties": {
                "selector": {"type": "object"},
                "to": {"type": "object"},
                "by_x": {"type": "integer"},
                "by_y": {"type": "integer"},
            },
            "required": [],
        }
    }
    try:
        flowdoc.validate({"steps": [{"tool": "drag", "args": args}]}, schemas)
    except flowdoc.InvalidFlow as refused:
        return " ".join(refused.problems)
    return ""


def test_a_drag_with_no_element_is_refused_at_save():
    """It is a registered action now, so it has to be in the set the validator
    checks — otherwise the step saves cleanly and fails at run time, which is
    the whole thing save-time validation exists to prevent (Copilot, #31)."""
    assert "needs an element" in _drag_problems({"to": {"css": "#done"}})


def test_a_drag_with_no_destination_is_refused_at_save():
    assert "needs a destination" in _drag_problems({"selector": {"css": "#card"}})


def test_a_drag_with_two_kinds_of_destination_is_refused_at_save():
    problems = _drag_problems(
        {"selector": {"css": "#card"}, "to": {"css": "#done"}, "by_x": 10}
    )
    assert "not both" in problems


def test_a_drag_to_where_it_already_is_is_refused_at_save():
    assert "already is" in _drag_problems({"selector": {"css": "#card"}, "by_x": 0, "by_y": 0})


@pytest.mark.parametrize(
    "args",
    [
        {"selector": {"css": "#card"}, "to": {"css": "#done"}},
        {
            "selector": {"xpath": "//div[@id='card']"},
            "to": {"xpath": "//div[@id='done']"},
        },
        {"selector": {"css": "input[type=range]"}, "by_x": 120},
        {"selector": {"css": "input[type=range]"}, "by_y": -40},
    ],
    ids=["css to css", "xpath to xpath", "by x", "negative by y"],
)
def test_a_drag_that_says_both_ends_saves(args):
    assert _drag_problems(args) == ""


# ---- what a save leaves on disk (S21) -----------------------------------------


def test_a_saved_document_reads_back_byte_equal(store, tmp_path):
    """Comments, key order and non-ASCII text are what a person wrote, and a
    shorter rewrite leaves nothing of the longer one behind — whichever way the
    write is done."""
    long = "# the one that logs us in\nname: login\nnote: 'é — ünïcode'\nsteps: []\n" * 3
    short = "# short\nsteps: []"  # and no trailing newline
    store.write_text("bot", "login", long)
    assert (tmp_path / "bot" / "flows" / "login.yaml").read_bytes() == long.encode()
    store.write_text("bot", "login", short)
    assert (tmp_path / "bot" / "flows" / "login.yaml").read_bytes() == short.encode()
    assert store.read_text("bot", "login") == short


def test_a_write_cut_short_leaves_the_last_document_whole(store, tmp_path, monkeypatch):
    """A crash, a full disk or an NFS hiccup part way through a write used to
    leave a truncated document that reads as missing. The old one now stays
    until the new one is complete, and nothing half-written is left beside it."""
    store.save("bot", "login", {"description": "the good one", "steps": []})
    path = tmp_path / "bot" / "flows" / "login.yaml"
    before = path.read_bytes()

    def interrupted(*_):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(OSError):
        store.save("bot", "login", {"description": "the new one", "steps": []})
    with pytest.raises(OSError):
        store.write_text("bot", "login", "steps: []\n")
    monkeypatch.undo()
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir()] == ["login.yaml"]


def test_a_saved_document_keeps_its_emoji_as_written(store):
    """The admin editor shows the YAML text, so a save writes what the author
    wrote: an emoji stays an emoji, not a `\\U0001F600` escape."""
    store.save("bot", "smile", {"description": "ship it 😀", "steps": []})
    assert "ship it 😀" in store.read_text("bot", "smile")


def test_a_replaced_document_keeps_its_mode(store, tmp_path):
    """Rewritten in place, a file kept whatever mode an operator gave it; a
    rename over it must not quietly reset that."""
    store.save("bot", "login", {"steps": []})
    path = tmp_path / "bot" / "flows" / "login.yaml"
    path.chmod(0o640)
    store.save("bot", "login", {"description": "again", "steps": []})
    assert path.stat().st_mode & 0o777 == 0o640
    store.write_text("bot", "login", "steps: []\n")
    assert path.stat().st_mode & 0o777 == 0o640


def test_workspaces_live_under_workspaces(tmp_path):
    store = flowstore.from_settings(DataSettings(dir=str(tmp_path)))
    store.write_file("bot", "a.txt", b"x")
    assert (tmp_path / "workspaces" / "bot" / "files" / "a.txt").read_bytes() == b"x"


def test_an_old_layout_stops_the_boot_and_names_what_to_move(tmp_path):
    (tmp_path / "claudecode" / "screenshots").mkdir(parents=True)
    (tmp_path / "global" / "flows").mkdir(parents=True)
    (tmp_path / "recordings").mkdir()  # the inbox is not a workspace
    with pytest.raises(ConfigError) as exc:
        flowstore.from_settings(DataSettings(dir=str(tmp_path)))
    assert "claudecode, global" in str(exc.value)
    assert "workspaces/" in str(exc.value)


@pytest.mark.parametrize("inner", ["files", "flows", "screenshots", "x/files"])
def test_the_recordings_inbox_is_never_an_old_workspace(tmp_path, inner):
    (tmp_path / "recordings" / inner).mkdir(parents=True)
    assert flowstore.from_settings(DataSettings(dir=str(tmp_path))) is not None


def test_a_configured_inbox_beside_the_workspaces_is_never_an_old_workspace(tmp_path):
    (tmp_path / "inbox2" / "files").mkdir(parents=True)
    with pytest.raises(ConfigError):
        flowstore.from_settings(DataSettings(dir=str(tmp_path)))
    store = flowstore.from_settings(
        DataSettings(dir=str(tmp_path)), inbox=str(tmp_path / "inbox2")
    )
    assert store is not None
    (tmp_path / "real" / "flows").mkdir(parents=True)
    with pytest.raises(ConfigError) as exc:
        flowstore.from_settings(DataSettings(dir=str(tmp_path)), inbox=str(tmp_path / "inbox2"))
    assert "real" in str(exc.value) and "inbox2" not in str(exc.value)


def test_a_nested_inbox_is_not_an_old_workspace(tmp_path):
    (tmp_path / "inbox" / "files").mkdir(parents=True)
    (tmp_path / "inbox" / "files" / "x.mp4").write_bytes(b"x")
    inbox = tmp_path / "inbox" / "files"
    assert flowstore.from_settings(DataSettings(dir=str(tmp_path)), inbox=str(inbox))


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


def test_an_old_workspace_named_workspaces_is_refused(tmp_path):
    _touch(tmp_path / "workspaces" / "flows" / "a.yaml")
    with pytest.raises(ConfigError, match=r"`workspaces`.*reserved.*workspaces-old"):
        flowstore.from_settings(DataSettings(dir=str(tmp_path)))


def test_a_new_workspace_named_flows_boots(tmp_path):
    _touch(tmp_path / "workspaces" / "flows" / "flows" / "a.yaml")
    assert flowstore.from_settings(DataSettings(dir=str(tmp_path))) is not None


def test_an_old_workspace_named_recordings_is_refused(tmp_path):
    _touch(tmp_path / "recordings" / "flows" / "a.yaml")
    with pytest.raises(ConfigError, match=r"`recordings`.*reserved"):
        flowstore.from_settings(DataSettings(dir=str(tmp_path)))


def test_a_video_under_recordings_screenshots_boots(tmp_path):
    _touch(tmp_path / "recordings" / "screenshots" / "x.mp4")
    assert flowstore.from_settings(DataSettings(dir=str(tmp_path))) is not None


def test_an_image_under_recordings_screenshots_is_an_old_workspace(tmp_path):
    _touch(tmp_path / "recordings" / "screenshots" / "SHOT.PNG")
    with pytest.raises(ConfigError, match=r"`recordings`"):
        flowstore.from_settings(DataSettings(dir=str(tmp_path)))


@pytest.mark.parametrize("folder", ["files", "screenshots"])
def test_an_old_workspaces_folder_holding_a_file_is_refused(tmp_path, folder):
    _touch(tmp_path / "workspaces" / folder / "a.bin")
    with pytest.raises(ConfigError, match=r"`workspaces`"):
        flowstore.from_settings(DataSettings(dir=str(tmp_path)))


def test_a_symlinked_workspaces_flows_is_skipped(tmp_path):
    _touch(tmp_path / "elsewhere" / "a.yaml")
    (tmp_path / "workspaces").mkdir()
    (tmp_path / "workspaces" / "flows").symlink_to(tmp_path / "elsewhere")
    assert flowstore.from_settings(DataSettings(dir=str(tmp_path))) is not None


@pytest.mark.parametrize("fn_name", ["stat", "scandir"])
def test_a_fault_reading_a_reserved_folder_stops_the_boot(tmp_path, monkeypatch, fn_name):
    _touch(tmp_path / "workspaces" / "flows" / "a.yaml")
    _stat_eio_on(monkeypatch, fn_name, "flows")
    with pytest.raises(ConfigError, match="OSError"):
        flowstore.from_settings(DataSettings(dir=str(tmp_path)))


def test_an_inbox_with_a_transport_prefix_boots(tmp_path):
    _touch(tmp_path / "recordings" / "files" / "x.mp4")
    assert flowstore.from_settings(DataSettings(dir=str(tmp_path))) is not None


def _stat_eio_on(monkeypatch, fn_name, needle):
    import errno
    import os

    real = getattr(os, fn_name)

    def faulty(path, *a, **k):
        if str(path).endswith(needle):
            raise OSError(errno.EIO, "EIO")
        return real(path, *a, **k)

    monkeypatch.setattr(os, fn_name, faulty)


def test_an_unreadable_data_dir_stops_the_boot_not_an_empty_one(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "claudecode" / "files").mkdir(parents=True)
    _stat_eio_on(monkeypatch, "stat", "data")
    with pytest.raises(ConfigError) as exc:
        flowstore.from_settings(DataSettings(dir=str(root)))
    assert "data" in str(exc.value) and "OSError" in str(exc.value)


@pytest.mark.parametrize("fn_name", ["stat", "lstat"])
def test_an_unreadable_top_level_entry_stops_the_boot(tmp_path, monkeypatch, fn_name):
    (tmp_path / "claudecode" / "files").mkdir(parents=True)
    _stat_eio_on(monkeypatch, fn_name, "claudecode")
    with pytest.raises(ConfigError, match="OSError"):
        flowstore.from_settings(DataSettings(dir=str(tmp_path)))


def test_a_missing_data_dir_is_no_old_layout(tmp_path):
    assert flowstore.old_layout(tmp_path / "nope") == []


def test_a_recording_dir_nothing_collects_from_hides_no_old_workspace(tmp_path):
    from kubed.selenium_flow import config
    from kubed.selenium_flow.server import SeleniumMCP

    (tmp_path / "bot" / "flows").mkdir(parents=True)
    with pytest.raises(ConfigError, match="bot"):
        SeleniumMCP(config.Settings(
            grid={"url": "http://grid.invalid:4444"},
            data={"dir": str(tmp_path)},
            recording={"dir": str(tmp_path / "bot" / "inbox")},
        ))


def test_the_sessions_folder_becomes_the_workspaces_folder_once(tmp_path):
    flows = tmp_path / "sessions" / "desk" / "flows"
    flows.mkdir(parents=True)
    (flows / "login.yaml").write_text("steps: []\n")
    store = flowstore.from_settings(DataSettings(dir=str(tmp_path)))
    assert store is not None
    assert not (tmp_path / "sessions").exists()
    assert (tmp_path / "workspaces" / "desk" / "flows" / "login.yaml").is_file()
    # A second boot finds nothing to move and stays up.
    assert flowstore.from_settings(DataSettings(dir=str(tmp_path))) is not None


def test_both_folders_stop_the_boot_naming_both(tmp_path):
    (tmp_path / "sessions" / "a").mkdir(parents=True)
    (tmp_path / "workspaces" / "b").mkdir(parents=True)
    with pytest.raises(ConfigError) as exc:
        flowstore.from_settings(DataSettings(dir=str(tmp_path)))
    message = str(exc.value)
    assert str(tmp_path / "sessions") in message
    assert str(tmp_path / "workspaces") in message
    assert (tmp_path / "sessions" / "a").is_dir()  # nothing was moved


def test_a_fresh_data_directory_has_nothing_to_move(tmp_path):
    assert flowstore.from_settings(DataSettings(dir=str(tmp_path))) is not None
    assert not (tmp_path / "sessions").exists()


def test_an_old_flat_workspace_named_sessions_is_refused_and_not_moved(tmp_path):
    _touch(tmp_path / "sessions" / "flows" / "a.yaml")
    _touch(tmp_path / "bot" / "flows" / "b.yaml")
    with pytest.raises(ConfigError, match=r"sessions.*bot|bot.*sessions"):
        flowstore.from_settings(DataSettings(dir=str(tmp_path)))
    assert (tmp_path / "sessions" / "flows" / "a.yaml").exists()
    assert not (tmp_path / "workspaces").exists()


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks here")
def test_a_symlinked_sessions_folder_is_renamed_as_the_link(tmp_path):
    (tmp_path / "real" / "desk" / "flows").mkdir(parents=True)
    (tmp_path / "sessions").symlink_to(tmp_path / "real")
    assert flowstore.from_settings(DataSettings(dir=str(tmp_path))) is not None
    assert (tmp_path / "workspaces").is_symlink()
    assert not os.path.lexists(tmp_path / "sessions")
    assert (tmp_path / "real" / "desk" / "flows").is_dir()


def test_a_failed_move_stops_the_boot_as_unreadable(tmp_path, monkeypatch):
    (tmp_path / "sessions" / "a").mkdir(parents=True)

    def refuse(self, target):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("pathlib.Path.rename", refuse)
    with pytest.raises(ConfigError, match="cannot be read"):
        flowstore.from_settings(DataSettings(dir=str(tmp_path)))
