"""A session keeps files in two folders: Files, and its screenshots (§F4.7).

Downloads are not here — they are the Grid's. What is asserted is that the two
folders are separate, that the folder is part of every address, and that the
one bulk operation the store offers refuses the folder a person curated.
"""

import pytest

from kubed.selenium_flow.flows import library as flows

pytestmark = pytest.mark.unit

S = "desktop"


@pytest.fixture
def store(tmp_path):
    return flows.LocalFlowStore(tmp_path)


def test_the_folders_are_named_once():
    assert flows.FOLDERS == ("files", "screenshots")


def test_a_screenshot_lands_in_its_own_folder(store, tmp_path):
    store.create_file(S, "shot.png", b"png", flows.SCREENSHOTS_DIR)
    assert (tmp_path / S / "screenshots" / "shot.png").read_bytes() == b"png"
    assert not (tmp_path / S / "files").exists()


def test_the_default_folder_is_files(store, tmp_path):
    store.write_file(S, "report.pdf", b"pdf")
    assert (tmp_path / S / "files" / "report.pdf").exists()


def test_each_folder_lists_only_itself(store):
    store.create_file(S, "a.png", b"1", flows.SCREENSHOTS_DIR)
    store.write_file(S, "b.pdf", b"2")
    assert [f["name"] for f in store.files(S, flows.SCREENSHOTS_DIR)] == ["a.png"]
    assert [f["name"] for f in store.files(S)] == ["b.pdf"]


def test_one_name_can_live_in_both_folders(store):
    store.create_file(S, "shot.png", b"screenshot", flows.SCREENSHOTS_DIR)
    store.write_file(S, "shot.png", b"kept")
    assert store.read_file(S, "shot.png", flows.SCREENSHOTS_DIR) == b"screenshot"
    assert store.read_file(S, "shot.png") == b"kept"


def test_a_delete_names_its_folder(store):
    store.create_file(S, "shot.png", b"1", flows.SCREENSHOTS_DIR)
    store.write_file(S, "shot.png", b"2")
    assert store.delete_file(S, "shot.png", flows.SCREENSHOTS_DIR) is True
    assert store.read_file(S, "shot.png") == b"2"


def test_clearing_screenshots_counts_what_went_and_leaves_files(store):
    for n in ("a.png", "b.png"):
        store.create_file(S, n, b"x", flows.SCREENSHOTS_DIR)
    store.write_file(S, "keep.pdf", b"y")
    assert store.clear_folder(S, flows.SCREENSHOTS_DIR) == 2
    assert store.files(S, flows.SCREENSHOTS_DIR) == []
    assert [f["name"] for f in store.files(S)] == ["keep.pdf"]


def test_clearing_a_folder_that_does_not_exist_is_zero(store):
    assert store.clear_folder(S, flows.SCREENSHOTS_DIR) == 0


def test_files_cannot_be_cleared_wholesale(store):
    """Everything in Files was put there on purpose (§F4.1)."""
    store.write_file(S, "keep.pdf", b"y")
    with pytest.raises(flows.InvalidName):
        store.clear_folder(S, flows.FILES_DIR)
    assert store.read_file(S, "keep.pdf") == b"y"


@pytest.mark.parametrize("folder", ["downloads", "flows", "../files", ""])
def test_an_unknown_folder_is_refused(store, folder):
    with pytest.raises(flows.InvalidName):
        store.files(S, folder)
