import pytest

from kubed.selenium_flow import names
from kubed.selenium_flow.http import files
from kubed.selenium_flow.mcp import show


def test_an_old_uri_names_its_new_spelling():
    with pytest.raises(ValueError) as exc:
        names.retired_uri("session://files/screenshots/a.png")
    assert str(exc.value) == (
        "`session://files/screenshots/a.png` is now "
        "`workspace://files/screenshots/a.png`"
    )


def test_a_current_uri_passes():
    names.retired_uri("workspace://current")
    names.retired_uri("flow://flows")


def test_a_saved_flows_old_upload_uri_says_where_it_went():
    with pytest.raises(ValueError, match=r"is now `workspace://files/a\.png`"):
        files.parse_uri("session://files/a.png")


def test_show_names_the_new_spelling_of_an_old_uri():
    with pytest.raises(ValueError, match="is now `workspace://current`"):
        show.view_for("session://current")


@pytest.mark.parametrize("old, new", [
    ("session://files/downloads/export.csv", "session://downloads/export.csv"),  # Review Focus 2
    ("session://files/a.png", "workspace://files/a.png"),
    ("session://console?level=error", "workspace://console?level=error"),
])
def test_a_moved_uri_names_where_it_is_now(old, new):  # ruling 11
    with pytest.raises(ValueError) as exc:
        names.retired_uri(old)
    assert str(exc.value) == f"`{old}` is now `{new}`"


@pytest.mark.parametrize("uri", [
    "session://downloads", "session://downloads/export.csv",
    "workspace://files/downloads (1)", "workspace://files/screenshots/a.png",
])
def test_a_served_uri_passes(uri):
    names.retired_uri(uri)
