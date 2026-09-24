"""A session's files as three sections, addressed by path (§F4.6, §F4.7)."""

import json

import pytest

from kubed.selenium_flow.flows import library as flows
from kubed.selenium_flow.http import files

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


class Sessions:
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
        ("files", "report.pdf", "session://files/report.pdf"),
        ("screenshots", "shot.png", "session://files/screenshots/shot.png"),
        ("downloads", "export.csv", "session://files/downloads/export.csv"),
        ("files", "Q3 summary (1).csv", "session://files/Q3%20summary%20%281%29.csv"),
    ],
)
def test_a_uri_round_trips(folder, name, uri):
    assert files.uri_of(folder, name) == uri
    assert files.parse_uri(uri) == (folder, name)


def test_a_literal_space_parses_like_its_escape():
    assert files.parse_uri("session://files/screenshots/shot (1).png") == (
        "screenshots",
        "shot (1).png",
    )


@pytest.mark.parametrize(
    "uri",
    [
        "session://files",
        "session://files/screenshots",
        "session://files/downloads",
        "session://files/a/b",
        "session://files/flows/x",
        "flow://flows/x",
        "",
    ],
)
def test_a_uri_that_names_no_file_is_refused_with_the_shapes(uri):
    with pytest.raises(ValueError, match="session://files/screenshots/"):
        files.parse_uri(uri)


# ---- listings -----------------------------------------------------------------


def test_the_root_lists_files_and_names_two_folders(store):
    store.write_file(S, "report.pdf", b"p")
    store.create_file(S, "shot.png", b"s", flows.SCREENSHOTS_DIR)
    got = files.root(Actions(), Sessions(), store, TOKEN, S)
    assert [f["name"] for f in got["files"]] == ["report.pdf"]
    assert got["count"] == 1
    assert got["folders"] == [
        {"name": "screenshots", "uri": "session://files/screenshots", "count": 1},
        {"name": "downloads", "uri": "session://files/downloads", "count": 2, "browser": True},
    ]


def test_with_no_browser_the_downloads_folder_says_so(store):
    got = files.root(Actions(), Sessions(browser=""), store, TOKEN, S)
    assert got["folders"][1] == {
        "name": "downloads", "uri": "session://files/downloads", "count": 0, "browser": False,
    }


def test_every_entry_carries_its_own_uri_and_no_kept_flag(store):
    store.create_file(S, "shot.png", b"s", flows.SCREENSHOTS_DIR)
    shot = files.folder(Actions(), Sessions(), store, TOKEN, S, "screenshots")["files"][0]
    assert shot["uri"] == "session://files/screenshots/shot.png"
    assert "kept" not in shot
    assert shot["keep_with"] == 'keep_file("session://files/screenshots/shot.png")'


def test_a_file_in_files_has_nothing_to_keep(store):
    store.write_file(S, "report.pdf", b"p")
    entry = files.root(Actions(), Sessions(), store, TOKEN, S)["files"][0]
    assert "keep_with" not in entry


def test_the_same_name_in_two_folders_is_two_entries(store):
    store.write_file(S, "shot.png", b"kept")
    store.create_file(S, "shot.png", b"new", flows.SCREENSHOTS_DIR)
    got = files.sections(Actions(), Sessions(), store, TOKEN, S)
    assert [f["uri"] for f in got["files"]] == ["session://files/shot.png"]
    assert [f["uri"] for f in got["screenshots"]] == ["session://files/screenshots/shot.png"]


def test_sections_are_newest_first_and_name_the_app_component(store, tmp_path):
    import os

    for i, n in enumerate(["old.png", "new.png"]):
        store.create_file(S, n, b"x", flows.SCREENSHOTS_DIR)
        os.utime(tmp_path / S / "screenshots" / n, (1_700_000_000 + i, 1_700_000_000 + i))
    got = files.sections(Actions(), Sessions(), store, TOKEN, S)
    assert got["component"] == "fileSections"
    assert [f["name"] for f in got["screenshots"]] == ["new.png", "old.png"]
    assert [f["name"] for f in got["downloads"]] == ["export.csv", "screenshots"]


def test_each_folder_signs_its_own_route(store):
    store.write_file(S, "a.pdf", b"1")
    store.create_file(S, "b.png", b"2", flows.SCREENSHOTS_DIR)
    got = files.sections(Actions(), Sessions(), store, TOKEN, S)
    assert got["files"][0]["url"].startswith("/kept/desktop/a.pdf?")
    assert got["screenshots"][0]["url"].startswith("/screenshots/desktop/b.png?")
    assert got["downloads"][0]["url"].startswith("/files/abc/export.csv?")


# ---- keeping --------------------------------------------------------------------


def test_keeping_a_screenshot_moves_it_into_files(store):
    store.create_file(S, "shot.png", b"png", flows.SCREENSHOTS_DIR)
    got = files.keep(Actions(), Sessions(), store, "session://files/screenshots/shot.png")
    assert got["uri"] == "session://files/shot.png"
    assert got["from"] == "session://files/screenshots/shot.png"
    assert store.read_file(S, "shot.png") == b"png"
    assert store.files(S, flows.SCREENSHOTS_DIR) == []


def test_a_moved_screenshot_never_overwrites_a_kept_file(store):
    store.write_file(S, "shot.png", b"kept earlier")
    store.create_file(S, "shot.png", b"new", flows.SCREENSHOTS_DIR)
    got = files.keep(Actions(), Sessions(), store, "session://files/screenshots/shot.png")
    assert got["name"] == "shot (1).png"
    assert store.read_file(S, "shot.png") == b"kept earlier"
    assert store.read_file(S, "shot (1).png") == b"new"


def test_keeping_a_download_copies_it_and_replaces_a_same_named_file(store):
    store.write_file(S, "export.csv", b"old copy")
    grid = Grid(data=b"fresh")
    got = files.keep(Actions(grid), Sessions(), store, "session://files/downloads/export.csv")
    assert got["uri"] == "session://files/export.csv"
    assert store.read_file(S, "export.csv") == b"fresh"
    assert grid.read == [("abc", "export.csv")]


@pytest.mark.parametrize("name", ["screenshots", "downloads"])
def test_a_reserved_name_lands_beside_itself(store, name):
    got = files.keep(Actions(), Sessions(), store, f"session://files/downloads/{name}")
    assert got["name"] == f"{name} (1)"


def test_keeping_a_file_already_in_files_answers_with_it(store):
    store.write_file(S, "report.pdf", b"p")
    got = files.keep(Actions(), Sessions(), store, "session://files/report.pdf")
    assert got["uri"] == "session://files/report.pdf"


def test_keeping_a_screenshot_that_is_gone_says_where_to_look(store):
    with pytest.raises(ValueError, match="session://files/screenshots"):
        files.keep(Actions(), Sessions(), store, "session://files/screenshots/nope.png")


def test_keeping_a_download_with_no_browser_is_refused(store):
    with pytest.raises(ValueError, match="browser"):
        files.keep(Actions(), Sessions(browser=""), store, "session://files/downloads/export.csv")


def test_keeping_refuses_when_there_is_nowhere_to_keep():
    with pytest.raises(ValueError, match="FLOW_DATA_DIR"):
        files.keep(Actions(), Sessions(), None, "session://files/screenshots/a.png")


# ---- bytes this server made -------------------------------------------------


def test_a_screenshot_is_kept_in_screenshots_with_a_link(store):
    got = files.keep_made(Sessions(), store, "screenshot.png", b"p", TOKEN, folder="screenshots")
    assert got["uri"] == "session://files/screenshots/screenshot.png"
    again = files.keep_made(Sessions(), store, "screenshot.png", b"q", TOKEN, folder="screenshots")
    assert again["name"] == "screenshot (1).png"


def test_a_print_is_kept_in_files(store):
    got = files.keep_made(Sessions(), store, "page.pdf", b"p", TOKEN)
    assert got["uri"] == "session://files/page.pdf"


# ---- reading back, clearing, deleting ------------------------------------------


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("session://files/report.pdf", ("report.pdf", b"kept")),
        ("session://files/screenshots/shot.png", ("shot.png", b"png")),
        ("session://files/downloads/export.csv", ("export.csv", b"from-grid")),
    ],
)
def test_any_file_can_be_read_back_by_its_uri(store, uri, expected):
    store.write_file(S, "report.pdf", b"kept")
    store.create_file(S, "shot.png", b"png", flows.SCREENSHOTS_DIR)
    assert files.read_file(Actions(), Sessions(), store, uri) == expected


def test_reading_a_name_nobody_has_is_the_callers_mistake(store):
    with pytest.raises(ValueError, match=r"session://files"):
        files.read_file(Actions(), Sessions(), store, "session://files/nope.pdf")


def test_clearing_screenshots_reports_how_many(store):
    store.create_file(S, "a.png", b"1", flows.SCREENSHOTS_DIR)
    store.write_file(S, "keep.pdf", b"2")
    assert files.clear_screenshots(store, S) == {"cleared": 1, "session": S}
    assert store.read_file(S, "keep.pdf") == b"2"


def test_json_for_keep_with_survives_a_quote_in_a_name(store):
    store.create_file(S, 'Q4 "final".png', b"1", flows.SCREENSHOTS_DIR)
    entry = files.folder(Actions(), Sessions(), store, TOKEN, S, "screenshots")["files"][0]
    call = entry["keep_with"]
    assert json.loads(call[len("keep_file("):-1]) == entry["uri"]
