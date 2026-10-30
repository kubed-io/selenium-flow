"""A workspace's files as three sections, addressed by path (§F4.6, §F4.7)."""

import json
import re

import pytest
import requests

from kubed.selenium_flow.faults import NoSession
from kubed.selenium_flow.flows import store as flows
from kubed.selenium_flow.http import files
from kubed.selenium_flow.names import FILES_DIR, SCREENSHOTS_DIR

pytestmark = pytest.mark.unit

S = "desktop"
TOKEN = "t"
DOWNLOADS = [
    {"name": "export.csv", "size": 3, "creationTime": 1700000002000},
    {"name": "screenshots", "size": 1, "creationTime": 1700000001000},
]


class Grid:
    def __init__(self, entries=None, data=b"from-grid"):
        self.entries = entries if entries is not None else list(DOWNLOADS)
        self.data = data
        self.read = []

    def files(self, session_id):
        return self.entries

    def read_file(self, session_id, name):
        self.read.append((session_id, name))
        return self.data


class Actions:
    def __init__(self, grid=None):
        self.grid = grid or Grid()


class FakeWorkspaces:
    def __init__(self, browser="abc"):
        self._browser = browser

    def name(self):
        return S

    def browser(self, name):
        return self._browser

    def resolve(self, name):
        return self._browser


@pytest.fixture
def store(tmp_path):
    return flows.LocalFlowStore(tmp_path)


# ---- addresses ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("folder", "name", "uri"),
    [
        ("files", "report.pdf", "workspace://files/report.pdf"),
        ("screenshots", "shot.png", "workspace://files/screenshots/shot.png"),
        ("downloads", "export.csv", "session://downloads/export.csv"),
        ("files", "Q3 summary (1).csv", "workspace://files/Q3%20summary%20%281%29.csv"),
    ],
)
def test_a_uri_round_trips(folder, name, uri):
    assert files.uri_of(folder, name) == uri
    assert files.parse_uri(uri) == (folder, name)


def test_a_literal_space_parses_like_its_escape():
    assert files.parse_uri("workspace://files/screenshots/shot (1).png") == (
        "screenshots",
        "shot (1).png",
    )


@pytest.mark.parametrize(
    "uri",
    [
        "workspace://files",
        "workspace://files/screenshots",
        "session://downloads",
        "session://downloads/a/b",
        "workspace://files/a/b",
        "workspace://files/flows/x",
        "flow://flows/x",
        "",
    ],
)
def test_a_uri_that_names_no_file_is_refused_with_the_shapes(uri):
    with pytest.raises(ValueError, match="workspace://files/screenshots/"):
        files.parse_uri(uri)


# ---- listings -----------------------------------------------------------------


def test_the_root_lists_files_and_names_two_folders(store):
    store.write_file(S, "report.pdf", b"p")
    store.create_file(S, "shot.png", b"s", SCREENSHOTS_DIR)
    got = files.root(Actions(), FakeWorkspaces(), store, TOKEN, S)
    assert [f["name"] for f in got["files"]] == ["report.pdf"]
    assert got["count"] == 1
    assert got["folders"] == [
        {"name": "screenshots", "uri": "workspace://files/screenshots", "count": 1},
        {"name": "recordings", "uri": "workspace://files/recordings", "count": 0},
    ]


def test_a_moved_download_uri_says_where_it_went():  # Review Focus 1
    with pytest.raises(ValueError, match=re.escape("is now `session://downloads/export.csv`")):
        files.parse_uri("workspace://files/downloads/export.csv")


def test_the_root_never_asks_the_grid(store):  # ruling 10, Review Focus 4
    class Down:
        def files(self, _session_id):
            raise requests.ConnectionError("grid down")
    store.write_file(S, "report.pdf", b"p")
    got = files.root(Actions(Down()), FakeWorkspaces(), store, TOKEN, S)
    assert [f["name"] for f in got["files"]] == ["report.pdf"]


def test_without_a_data_dir_files_is_off_even_with_a_browser():
    for read in (lambda: files.root(Actions(), FakeWorkspaces(), None, TOKEN, S),
                 lambda: files.folder(Actions(), FakeWorkspaces(), None, TOKEN, S, "screenshots")):
        with pytest.raises(ValueError, match=re.escape(files.OFF)):
            read()


def test_downloads_with_no_session_say_so(store):  # rulings 3, 8
    idle = FakeWorkspaces(browser="")
    for call in (
        lambda: files.folder(Actions(), idle, store, TOKEN, S, "downloads"),
        lambda: files.read_file(Actions(), idle, store, "session://downloads/export.csv", S),
        lambda: files.keep(Actions(), idle, store, "session://downloads/export.csv", S),
    ):
        with pytest.raises(NoSession) as exc:
            call()
        assert str(exc.value) == files.NO_DOWNLOADS


def test_downloads_with_no_store_and_no_session_say_no_session():
    with pytest.raises(NoSession) as exc:
        files.folder(Actions(), FakeWorkspaces(browser=""), None, TOKEN, S, "downloads")
    assert str(exc.value) == files.NO_DOWNLOADS


def test_downloads_with_no_store_still_list_from_an_open_browser():
    got = files.folder(Actions(), FakeWorkspaces(), None, TOKEN, S, "downloads")
    assert [f["name"] for f in got["files"]] == ["export.csv", "screenshots"]


def test_the_downloads_listing_has_no_browser_flag(store):
    got = files.folder(Actions(), FakeWorkspaces(), store, TOKEN, S, "downloads")
    assert got["uri"] == "session://downloads" and "browser" not in got
    assert got["files"][0]["keep_with"].startswith('keep_file("session://downloads/')


def test_the_admins_sections_still_answer_with_no_browser(store):  # Review Focus 3
    got = files.sections(Actions(), FakeWorkspaces(), store, TOKEN, S, session_id="")
    assert got["downloads"] == [] and got["browser"] is False


def test_every_entry_carries_its_own_uri_and_no_kept_flag(store):
    store.create_file(S, "shot.png", b"s", SCREENSHOTS_DIR)
    shot = files.folder(Actions(), FakeWorkspaces(), store, TOKEN, S, "screenshots")["files"][0]
    assert shot["uri"] == "workspace://files/screenshots/shot.png"
    assert "kept" not in shot
    assert shot["keep_with"] == 'keep_file("workspace://files/screenshots/shot.png")'


def test_a_file_in_files_has_nothing_to_keep(store):
    store.write_file(S, "report.pdf", b"p")
    entry = files.root(Actions(), FakeWorkspaces(), store, TOKEN, S)["files"][0]
    assert "keep_with" not in entry


def test_the_same_name_in_two_folders_is_two_entries(store):
    store.write_file(S, "shot.png", b"kept")
    store.create_file(S, "shot.png", b"new", SCREENSHOTS_DIR)
    got = files.sections(Actions(), FakeWorkspaces(), store, TOKEN, S)
    assert [f["uri"] for f in got["files"]] == ["workspace://files/shot.png"]
    assert [f["uri"] for f in got["screenshots"]] == ["workspace://files/screenshots/shot.png"]


def test_sections_are_newest_first_and_name_the_app_component(store, tmp_path):
    import os

    for i, n in enumerate(["old.png", "new.png"]):
        store.create_file(S, n, b"x", SCREENSHOTS_DIR)
        os.utime(tmp_path / S / "screenshots" / n, (1_700_000_000 + i, 1_700_000_000 + i))
    got = files.sections(Actions(), FakeWorkspaces(), store, TOKEN, S)
    assert got["component"] == "fileSections"
    assert [f["name"] for f in got["screenshots"]] == ["new.png", "old.png"]
    assert [f["name"] for f in got["downloads"]] == ["export.csv", "screenshots"]


def test_each_folder_signs_its_own_route(store):
    store.write_file(S, "a.pdf", b"1")
    store.create_file(S, "b.png", b"2", SCREENSHOTS_DIR)
    got = files.sections(Actions(), FakeWorkspaces(), store, TOKEN, S)
    assert got["files"][0]["url"].startswith("/kept/desktop/a.pdf?")
    assert got["screenshots"][0]["url"].startswith("/screenshots/desktop/b.png?")
    assert got["downloads"][0]["url"].startswith("/files/abc/export.csv?")


# ---- keeping --------------------------------------------------------------------


def test_keeping_a_screenshot_moves_it_into_files(store):
    store.create_file(S, "shot.png", b"png", SCREENSHOTS_DIR)
    got = files.keep(Actions(), FakeWorkspaces(), store, "workspace://files/screenshots/shot.png", S)
    assert got["uri"] == "workspace://files/shot.png"
    assert got["from"] == "workspace://files/screenshots/shot.png"
    assert store.read_file(S, "shot.png") == b"png"
    assert store.files(S, SCREENSHOTS_DIR) == []


def test_a_moved_screenshot_never_overwrites_a_kept_file(store):
    store.write_file(S, "shot.png", b"kept earlier")
    store.create_file(S, "shot.png", b"new", SCREENSHOTS_DIR)
    got = files.keep(Actions(), FakeWorkspaces(), store, "workspace://files/screenshots/shot.png", S)
    assert got["name"] == "shot (1).png"
    assert store.read_file(S, "shot.png") == b"kept earlier"
    assert store.read_file(S, "shot (1).png") == b"new"


def test_keeping_a_download_copies_it_and_replaces_a_same_named_file(store):
    store.write_file(S, "export.csv", b"old copy")
    grid = Grid(data=b"fresh")
    got = files.keep(Actions(grid), FakeWorkspaces(), store, "session://downloads/export.csv", S)
    assert got["uri"] == "workspace://files/export.csv"
    assert store.read_file(S, "export.csv") == b"fresh"
    assert grid.read == [("abc", "export.csv")]


@pytest.mark.parametrize("name", ["screenshots", "downloads"])
def test_a_reserved_name_lands_beside_itself(store, name):
    got = files.keep(Actions(), FakeWorkspaces(), store, f"session://downloads/{name}", S)
    assert got["name"] == f"{name} (1)"


def test_keeping_a_file_already_in_files_answers_with_it(store):
    store.write_file(S, "report.pdf", b"p")
    got = files.keep(Actions(), FakeWorkspaces(), store, "workspace://files/report.pdf", S)
    assert got["uri"] == "workspace://files/report.pdf"


def test_keeping_a_screenshot_that_is_gone_says_where_to_look(store):
    with pytest.raises(ValueError, match="workspace://files/screenshots"):
        files.keep(Actions(), FakeWorkspaces(), store, "workspace://files/screenshots/nope.png", S)


def test_keeping_a_download_with_no_browser_is_refused(store):
    with pytest.raises(ValueError, match=re.escape(files.NO_DOWNLOADS)):
        files.keep(Actions(), FakeWorkspaces(browser=""), store, "session://downloads/export.csv", S)


def test_keeping_refuses_when_there_is_nowhere_to_keep():
    with pytest.raises(ValueError, match="DATA_DIR"):
        files.keep(Actions(), FakeWorkspaces(), None, "workspace://files/screenshots/a.png", S)


def test_a_screenshot_that_cannot_be_removed_leaves_no_copy_behind(store, monkeypatch):
    """A move is a copy plus a delete. If the delete fails after the copy has
    already claimed a name in Files, the file must not end up in both places —
    so the claimed copy is rolled back and the original error still surfaces."""
    store.create_file(S, "shot.png", b"png", SCREENSHOTS_DIR)

    original = flows.LocalFlowStore.delete_file

    def refuse(self, workspace, name, folder=FILES_DIR):
        if folder == SCREENSHOTS_DIR:
            raise PermissionError("read-only screenshots")
        return original(self, workspace, name, folder)

    monkeypatch.setattr(flows.LocalFlowStore, "delete_file", refuse)

    with pytest.raises(PermissionError):
        files.keep(Actions(), FakeWorkspaces(), store, "workspace://files/screenshots/shot.png", S)
    assert store.files(S) == []
    assert [f["name"] for f in store.files(S, SCREENSHOTS_DIR)] == ["shot.png"]


def test_a_screenshot_removed_by_someone_else_during_the_move_leaves_no_copy_behind(
    store, monkeypatch
):
    """`delete_file` returns False, rather than raising, when the name was
    already gone — a concurrent keep or a clear won the race between this
    call's `read_file` and its own `delete_file`. That is not "the move partly
    worked": the copy this call just claimed in Files is exactly as unearned
    as it would have been had `read_file` found nothing at all, so it is
    rolled back and the caller sees the same refusal."""
    store.create_file(S, "shot.png", b"png", SCREENSHOTS_DIR)

    original = flows.LocalFlowStore.delete_file

    def raced(self, workspace, name, folder=FILES_DIR):
        if folder == SCREENSHOTS_DIR:
            original(self, workspace, name, folder)  # a concurrent clear wins the race
            return False
        return original(self, workspace, name, folder)

    monkeypatch.setattr(flows.LocalFlowStore, "delete_file", raced)

    with pytest.raises(ValueError, match="no screenshot called"):
        files.keep(Actions(), FakeWorkspaces(), store, "workspace://files/screenshots/shot.png", S)
    assert store.files(S) == []
    assert store.files(S, SCREENSHOTS_DIR) == []


# ---- bytes this server made -------------------------------------------------


def test_a_screenshot_is_kept_in_screenshots_with_a_link(store):
    got = files.keep_made(S, store, "screenshot.png", b"p", TOKEN, folder="screenshots")
    assert got["uri"] == "workspace://files/screenshots/screenshot.png"
    again = files.keep_made(S, store, "screenshot.png", b"q", TOKEN, folder="screenshots")
    assert again["name"] == "screenshot (1).png"


def test_a_print_is_kept_in_files(store):
    got = files.keep_made(S, store, "page.pdf", b"p", TOKEN)
    assert got["uri"] == "workspace://files/page.pdf"


# ---- reading back, clearing, deleting ------------------------------------------


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("workspace://files/report.pdf", ("report.pdf", b"kept")),
        ("workspace://files/screenshots/shot.png", ("shot.png", b"png")),
        ("session://downloads/export.csv", ("export.csv", b"from-grid")),
    ],
)
def test_any_file_can_be_read_back_by_its_uri(store, uri, expected):
    store.write_file(S, "report.pdf", b"kept")
    store.create_file(S, "shot.png", b"png", SCREENSHOTS_DIR)
    assert files.read_file(Actions(), FakeWorkspaces(), store, uri, S) == expected


def test_reading_a_name_nobody_has_is_the_callers_mistake(store):
    with pytest.raises(ValueError, match=r"workspace://files"):
        files.read_file(Actions(), FakeWorkspaces(), store, "workspace://files/nope.pdf", S)


def test_clearing_screenshots_reports_how_many(store):
    store.create_file(S, "a.png", b"1", SCREENSHOTS_DIR)
    store.write_file(S, "keep.pdf", b"2")
    assert files.clear_screenshots(store, S) == {"cleared": 1, "workspace": S}
    assert store.read_file(S, "keep.pdf") == b"2"


def test_json_for_keep_with_survives_a_quote_in_a_name(store):
    store.create_file(S, 'Q4 "final".png', b"1", SCREENSHOTS_DIR)
    entry = files.folder(Actions(), FakeWorkspaces(), store, TOKEN, S, "screenshots")["files"][0]
    call = entry["keep_with"]
    assert json.loads(call[len("keep_file("):-1]) == entry["uri"]
