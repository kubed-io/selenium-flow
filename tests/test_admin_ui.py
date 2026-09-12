"""The admin page itself, as designed in §F1.36.

These are string assertions against the served document, which is crude and is
the same thing the existing page tests do — there is no DOM here. What they are
worth is catching the wiring that fails *silently*: a control rendered with no
handler, a confirm that asserts a count instead of showing a list, a mark that
looks like a button on a surface holding no credential.

The design decisions each one pins are in §F1.36 and §F1.10.
"""

import pytest
from starlette.testclient import TestClient

from .conftest import TOKEN  # noqa: F401 - the server fixture needs the module

pytestmark = pytest.mark.unit


@pytest.fixture
def page(server):
    return TestClient(server.mcp.http_app()).get("/admin").text


@pytest.fixture
def components(page):
    """Just the shared library, which is held to a stricter rule than the page."""
    return page.split("const SF")[1].split("})();")[0]


# ---- the shape of the detail view -------------------------------------------


def test_the_detail_view_is_two_accordions(page):
    """Dr K's shape, and the reason the page stays a single column: files and
    flows are the two things a session accumulates."""
    for section in ("filesSection", "flowsSection"):
        assert f'id="{section}"' in page, section
        assert f'data-toggle="{section}"' in page, f"{section} has no header to click"
    assert "box.setAttribute('data-open', String(!open));" in page


def test_the_last_page_gets_a_row_of_its_own_and_is_a_link(components):
    """A URL is twenty characters or two hundred, and it is the thing you came
    to read. The old header buried it in a seven-column grid."""
    assert '"lastpage"' in components
    assert 'target="_blank" rel="noopener noreferrer"' in components


def test_a_last_page_that_is_not_a_web_url_is_not_linked(components):
    """Escaping makes a value safe to display, not safe to click. `javascript:`
    is a URL too, and the page a session last visited is not ours to trust."""
    assert "const safeHref" in components
    assert "/^https?:\\/\\//i.test" in components


def test_the_header_no_longer_repeats_the_file_count(components):
    """The Files section carries it. Saying it in two places is an invitation
    for the two to disagree, and the header is the one that goes stale."""
    summary = components.split("function sessionSummary")[1].split("function ")[0]
    assert "files_count" not in summary


def test_the_header_groups_the_two_lifetimes(components):
    """What the SESSION keeps outlives the browser; what the BROWSER has goes
    with it. That is why they are two blocks and not one grid."""
    summary = components.split("function sessionSummary")[1].split("function ")[0]
    assert "group('session'" in summary and "group('browser'" in summary


# ---- files ------------------------------------------------------------------


def test_a_download_and_a_kept_file_carry_different_marks(components):
    """One list with a property saying which (§F1.10): a bubble for a download,
    a pin for a kept file."""
    assert 'class="mark bubble" data-keep=' in components
    assert 'class="mark pin" data-delete=' in components


def test_the_marks_are_inert_without_a_surface_that_can_act(components):
    """These tiles also render inside an MCP app holding no credential, where a
    live control would be a button that cannot work. The page that *can* act
    opts in; the library never assumes it."""
    assert "'files' + (opts.actions ? ' can-act' : '')" in components


def test_the_shared_library_still_renders_no_action_buttons(components):
    """The marks are spans with data attributes, and the page wires the clicks.
    That is what keeps this library rendering-only — see sessionList's onpick
    for the same pattern."""
    assert "createElement('button')" not in components


def test_only_a_kept_file_offers_a_delete(page):
    """The Grid's store has no per-file delete, so a trash on a download would
    be a control with nothing behind it. Only the pin reveals one."""
    assert ".can-act .mark.pin:hover .trash { display: block; }" in page
    assert "data-delete" in page


def test_clearing_downloads_lists_the_names_it_will_remove(page):
    """"Delete 12 files?" without saying which twelve is an assertion rather
    than a disclosure. The scope is shown."""
    assert "downloads.map((n) => '<li>' + SF.esc(n) + '</li>')" in page
    assert "filter((f) => !f.kept)" in page, "it would offer to clear kept files too"


def test_clearing_says_kept_files_are_untouched(page):
    """The objection that condemned the old button. It cannot happen once
    keeping is a copy, and the confirm is where that gets said."""
    assert "not</strong> touched" in page


# ---- flows ------------------------------------------------------------------


def test_a_flow_shows_its_steps_without_selectors_or_urls(page):
    """A step is its number, its tool and its id. Selectors and URLs are
    parameters, and they belong in the pane once a step is picked."""
    assert "'<span class=\"n\">' + (i + 1) + '</span>'" in page
    assert "function paramsOf(step)" in page
    assert "Pick a step to see what it passes." in page


def test_a_step_that_binds_a_secret_is_marked(page):
    assert "const bindsSecret" in page
    assert "value_from && step.params.value_from.secret" in page


def test_only_shared_flows_are_badged(page):
    """A badge on everything says nothing, so a session's own flows carry none."""
    assert "(f.shared ? '&#127760; ' : '')" in page


def test_the_move_button_is_one_verb_whose_label_flips(page):
    """There is no separate promote: a flow lives in exactly one directory, so
    the only action is which one (§F1.2)."""
    assert "(f.shared ? 'To this session' : 'To global')" in page
    assert "const to = doc.shared ? flowsData.session : 'global';" in page
    assert "'/move', 'POST', {to: to}" in page


def test_deleting_a_flow_warns_when_it_is_the_shared_one(page):
    """The wording has to cover both cases, because the button is the same one
    on a flow that every session runs."""
    assert "It is removed from the folder it lives in." in page
    assert "folder goes for every session" in page


def test_the_editor_is_handed_the_stored_yaml(page):
    """Not a re-dump of the parsed document — that loses the comments and the
    ordering a person chose, invisibly, the first time they press Save."""
    assert "box.querySelector('textarea').value = doc.yaml" in page
    assert "{yaml: sheet.querySelector('textarea').value}" in page


def test_flows_being_off_reads_as_off_rather_than_broken(page):
    assert "Flows are off: this server was started" in page


# ---- what a failure looks like ----------------------------------------------


def test_the_page_surfaces_the_servers_own_message(page):
    """These errors are written to be read: "the shared 'global' library is
    read-only…" says which rule was hit and what to do about it. A status code
    says none of that."""
    assert "said = (await res.json()).error" in page
    assert "said || 'request failed ('" in page
