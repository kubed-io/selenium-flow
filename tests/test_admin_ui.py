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


def test_an_accordion_can_be_opened_without_a_mouse(page):
    """It is the only way to open or close the section, so a styled span was a
    control keyboard and screen-reader users could not reach at all. A real
    button gets focus and Enter/Space for free; the state has to be announced
    rather than left to a caret nobody hears."""
    for section in ("filesSection", "flowsSection"):
        assert f'<button type="button" class="title" data-toggle="{section}"' in page
    assert 'aria-expanded="true" aria-controls="filesBody"' in page
    assert 'aria-expanded="true" aria-controls="flowsBody"' in page
    assert "tab.setAttribute('aria-expanded', String(!open));" in page


def test_the_file_marks_answer_the_keys_a_button_answers(page):
    """The marks carry role=button and a tab stop on this surface, so they have
    to behave like buttons. Space is preventDefault-ed or the page scrolls out
    from under the thing you were aiming at."""
    assert "$('files').addEventListener('keydown'" in page
    assert "e.key !== 'Enter' && e.key !== ' '" in page
    assert ".mark[role=button]" in page
    assert "e.preventDefault();\n  mark.click();" in page


# ---- a reply that arrives after you have moved on ---------------------------


def test_a_late_reply_cannot_render_one_session_under_another(page):
    """Every load is an await against a session the operator can navigate away
    from. This is not only a stale render: the action paths are built from
    `current`, so B's panel showing A's flows would let an edit, a move or a
    delete land on the wrong library."""
    assert "const gone = (key) => key !== current;" in page
    for loader in ("loadFiles", "loadFlows"):
        body = page.split(f"async function {loader}(key)")[1].split("\n}\n")[0]
        # Twice each: the success path and the catch. An error from A must not
        # blank B's panel any more than A's data may fill it. The guard also
        # carries the sequence check — see the overlap test below — so it is
        # `gone(key)` that is asserted rather than the whole condition.
        assert body.count("gone(key)") == 2, loader


def test_a_late_flow_cannot_overwrite_the_one_you_just_picked(page):
    """Picking A then B, with A slow, resolved A last and left B's name beside
    A's document — or, on the error path, B's panel stuck loading forever."""
    assert "if (gone(key) || flowName !== name) return;" in page
    assert page.count("if (gone(key) || flowName !== name) return;") == 2


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


def test_a_mark_is_only_focusable_where_it_actually_does_something(components):
    """Button semantics belong on the surface that wired a handler. Off, the
    mark is decoration, and a tab stop that does nothing when you press Enter is
    worse than no tab stop at all — so the attributes are gated on the same flag
    the styling is."""
    assert "const act = (label) => (opts.actions" in components
    assert "role=\"button\" tabindex=\"0\" aria-label=" in components


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
    assert "downloads.map((n) => '<li>' + SF.esc(n)" in page


def test_the_clear_confirm_does_not_build_its_list_from_the_merged_files(page):
    """`data.files` is de-duplicated: a download sharing a name with a kept file
    loses to it and vanishes from that list. The DELETE clears it regardless, so
    filtering the merge would name eleven of the twelve files it takes. The
    server sends the Grid's own listing for this."""
    assert "downloads = data.downloads || [];" in page
    assert "filter((f) => !f.kept)" not in page


def test_clearing_says_what_happens_to_each_name(page):
    """Listing every download and then saying kept files are untouched put a
    sentence and a list in contradiction on one screen: a kept file's NAME is
    in the deletion list, because its download really is deleted — and the file
    really does survive. Omitting those names would under-report what the
    button does, so the fate is said per row instead."""
    assert "keptNames.indexOf(n) === -1" in page
    assert "— gone" in page
    assert "kept copy stays" in page
    # And the list is still every download, because every download is deleted.
    assert "filter((f) => !f.kept)" not in page


def test_the_kept_names_come_from_the_merged_listing(page):
    """`data.downloads` is what the Grid holds; which of those also survive is
    only knowable from the merged list's `kept` flag."""
    assert "(data.files || []).filter((f) => f.kept).map((f) => f.name)" in page


# ---- flows ------------------------------------------------------------------


def test_a_flow_shows_its_steps_without_selectors_or_urls(page):
    """A step is its number, its tool and its id. Selectors and URLs are
    parameters, and they belong in the pane once a step is picked."""
    assert "'<span class=\"n\">' + (i + 1) + '</span>'" in page
    assert "function paramsOf(step)" in page
    assert "Pick a step to see what it passes." in page


def test_a_step_that_binds_a_secret_is_marked(page):
    """One argument, on one action. The mark keys off the argument existing
    rather than off a source name inside it (§F1.38)."""
    assert "const bindsSecret" in page
    assert "step.args && step.args.secret" in page


def test_a_parameter_reference_is_shown_as_written(page):
    """`${site}/login` is the argument. Seeing which arguments a parameter
    reaches is the point of reading a step, and unlike a secret there is
    nothing to hide — a parameter is non-secret by definition."""
    assert "const params = step.args || {};" in page
    assert "v.name + ' / ' + v.key" in page, "a secret still shows only its name"


def test_only_shared_flows_are_badged(page):
    """A badge on everything says nothing, so a session's own flows carry none."""
    assert "(f.shared ? '&#127760; ' : '')" in page


def test_there_is_no_move_button_when_both_ends_are_the_same_folder(page):
    """`global` is a name a caller may legitimately choose, and such a session's
    library *is* the shared one — so the move's source and target are the same
    place. The server answers `moved: false`, correctly; offering the button at
    all dresses a no-op as an action."""
    assert "flowsData && flowsData.session === 'global' ? '' :" in page


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


# ---- the panel that never repainted -----------------------------------------


def test_the_flows_panel_repaints_when_the_flows_change(page):
    """The bug a live server showed and no test could: sessions and files were
    pushed and applied, flows were pushed and *ignored*. `flows_count` had been
    in every heartbeat since the panel shipped and nothing read it — so a flow
    appearing or vanishing was invisible until you left the session and came
    back."""
    body = page.split("function refreshDetail")[1].split("\n}\n")[0]
    assert "loadFiles(current)" in body, "the file half is the pattern to match"
    assert "loadFlows(current)" in body


def test_neither_stamp_is_a_count(page):
    """Both panels watch a state token. A count cannot see a flow edited in
    place, and it cannot see a kept copy deleted while its download remains —
    in each case the number holds still while what is on screen changes."""
    assert "row.files_rev" in page
    assert "row.flows_rev" in page


def test_the_flows_stamp_is_not_a_count(page):
    """A flow edited in place keeps its name and its step count, and editing is
    what the panel is for — so a page watching the number would sit showing a
    document the server had already replaced."""
    assert "const flowsStamp = (row) => row.flows_rev;" in page
    assert "shownFlows = data.rev || null;" in page


def test_opening_a_session_forgets_what_the_last_one_showed(page):
    """Every stamp resets together, or the new session inherits the old one's
    and the first repaint is skipped."""
    assert "shownFiles = shownBrowser = shownFlows = null;" in page


def test_two_loads_of_the_same_panel_cannot_race_each_other(page):
    """`gone` does not catch this: both requests are for the *current* session,
    so a slow one for revision A can land after a fast one for B and render A's
    data while recording A's token. The heartbeat does correct it — the row
    still carries B — but only after showing the wrong thing and spending an
    extra fetch. The last request issued is the only one allowed to render."""
    assert "let filesSeq = 0, flowsSeq = 0;" in page
    for loader, seq in (("loadFiles", "filesSeq"), ("loadFlows", "flowsSeq")):
        body = page.split(f"async function {loader}(key)")[1].split("\n}\n")[0]
        assert f"const mine = ++{seq};" in body, loader
        # Both halves, as with `gone`: a late error must not paint over a
        # newer success either.
        assert body.count(f"mine !== {seq}") == 2, loader
