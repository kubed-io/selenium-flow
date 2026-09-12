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


def test_the_file_action_is_a_button_rather_than_a_span_that_acts(page):
    """It used to be a span wearing role=button and tabindex, which meant the
    page had to re-implement Enter and Space by hand — including the
    preventDefault that stops Space scrolling the page out from under the thing
    you were aiming at. A real button answers both keys, is in the tab order,
    and is announced as a button, for free. The hand-rolled keydown is gone
    because there is nothing left for it to do."""
    assert "$('files').addEventListener('keydown'" not in page
    assert '<button type="button" class="act keep"' in page
    assert '<button type="button" class="act drop"' in page


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
    a pin for a kept file. The mark says what the file IS and never acts, so it
    carries no data attribute for the page to wire — and it is labelled, so the
    state reaches someone who cannot see a pin."""
    assert 'class="mark bubble" role="img" aria-label="Download"' in components
    assert 'class="mark pin" role="img" aria-label="Kept"' in components
    assert 'class="mark bubble" data-keep=' not in components
    assert 'class="mark pin" data-delete=' not in components


def test_the_action_is_omitted_without_a_surface_that_can_act(components):
    """These tiles also render inside an MCP app holding no credential, where a
    live control would be a button that cannot work. The page that *can* act
    opts in; the library never assumes it. The STATUS mark is drawn either way,
    because what a file is stays true on every surface."""
    grid = components.split("function fileGrid")[1].split("function ")[0]
    assert "(opts.actions\n" in grid, "the action is gated"
    assert "grid.className = 'files';" in grid, "the status mark is not"


def test_each_glyph_has_one_meaning_and_one_corner(components):
    """Status right, action left — and never the same glyph in both. A pin was
    the kept mark on the right AND the keep button on the left, so clicking it
    looked like one mark jumping sides. The keep action is a plus: it is what
    produces the pin rather than another copy of it."""
    grid = components.split("function fileGrid")[1].split("function ")[0]
    assert "&#10133;" in grid, "keep is a plus"
    assert "&#128465;" in grid, "delete is a trash"
    assert grid.count("\U0001f4cc") == 1, "the pin appears once, as status"


def test_the_shared_library_renders_the_action_but_never_wires_it(components):
    """It emits a button with a data attribute; the page that turned actions on
    owns the click. That split is what lets the same tiles render inside an MCP
    app where the handler would have no credential to call with — see
    sessionList's onpick for the same pattern."""
    grid = components.split("function fileGrid")[1].split("function ")[0]
    assert "data-keep=" in grid and "data-delete=" in grid
    # The one listener here is the thumbnail's, which only opens a link.
    assert grid.count("addEventListener") == 1
    assert "item.querySelector('.thumb').addEventListener" in grid


def test_only_a_kept_file_offers_a_delete(page):
    """The Grid's store has no per-file delete, so a trash on a download would
    be a control with nothing behind it. A download's action keeps it; a kept
    file's is the delete."""
    grid = page.split("function fileGrid")[1].split("function ")[0]
    # `f.kept ? <delete> : <keep>` — one branch each, and neither borrows the
    # other's verb.
    kept, download = grid.split("? '<button")[1].split(": '<button")
    assert "data-delete" in kept and "data-keep" not in kept
    assert "data-keep" in download and "data-delete" not in download


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


def test_an_outline_row_is_an_icon_and_a_name(page):
    """A row names the thing and nothing else: the tool a step calls is a
    glyph, and the WORD for it is in the pane, which is what picking the row is
    for. Arguments never appear here at all — selectors and URLs are what you
    picked the step to read."""
    assert "function argsOf(step)" in page
    assert "Pick a parameter or a step to see what " in page
    row = page.split("function stepRow(")[1].split("\n}\n")[0]
    assert "TOOL_ICON[tool]" in row
    assert "SF.esc(s.id || tool)" in row
    assert "s.args" not in row


def test_the_outline_does_not_number_its_rows(page):
    """The list is already in order, so 1-2-3 down the left restated what the
    list said and cost the column the width it needed to sit BESIDE the detail
    rather than above it. A number is kept only where it is the point: the
    pane, and a parameter's `used by` citations, because a run report says
    "step 3 stopped the flow"."""
    row = page.split("function stepRow(")[1].split("\n}\n")[0]
    # `cite` is the "used by" case, and it is the only one that numbers.
    assert "(cite ? '<span class=\"n\">' + (i + 1) + '</span>' : '')" in row
    assert "stepRow(steps[i] || {}, i, true)" in page, "citations number"
    assert "steps.map((s, i) => stepRow(s, i))" in page, "the outline does not"


def test_required_is_a_star_on_a_parameter_and_nowhere_else(page):
    """A red star sits against the name it qualifies instead of as the word
    `required` at the far end of a 200px column. Steps carry no such mark:
    every step runs, so `required` on one would mean nothing."""
    param = page.split("function paramRow(")[1].split("\n}\n")[0]
    step = page.split("function stepRow(")[1].split("\n}\n")[0]
    assert 'class="req"' in param and 'aria-label="required"' in param
    assert "req" not in step


def test_a_parameters_glyph_is_its_type(page):
    """The one thing worth knowing about a parameter at a glance. Punctuation
    rather than emoji: these are JSON types, and at 12px `{}` is still `{}`
    while an emoji is a coloured smudge."""
    assert '"string": ' not in page
    icons = page.split("const TYPE_ICON = {")[1].split("};")[0]
    for ty in ("string", "number", "integer", "boolean", "object", "array"):
        assert ty in icons, ty


def test_every_runnable_action_has_a_glyph(page):
    """The row drops the tool's name, so a tool with no icon would be a step
    with no identity at all. The unknown glyph is reserved for a flow naming an
    action that does not exist — which the runner refuses, and which is meant
    to look wrong."""
    from kubed.selenium_flow.flowrun import RUNNABLE

    icons = page.split("const TOOL_ICON = {")[1].split("};")[0]
    for tool in RUNNABLE:
        assert f"{tool}:" in icons, tool


def test_a_row_still_says_its_tool_to_a_screen_reader(page):
    """Replacing the word with a picture takes the word away from anyone who
    cannot see the picture — and from anyone who has not learnt which emoji
    means `extract`. It moves to the label and the tooltip, not out of the
    page."""
    row = page.split("function stepRow(")[1].split("\n}\n")[0]
    assert """role="img" aria-label="' + SF.esc(tool)""" in row
    assert """title="' + SF.esc(tool) + '">""" in row


def test_the_detail_sits_beside_the_outline_rather_than_under_it(page):
    """It used to render under the steps, which put the answer below the
    question and left the right half of a wide panel empty. Three columns:
    what the flow is, and what the thing you picked out of it holds."""
    assert '<div class="panes"><div class="outline">' in page
    assert "'</div><div class=\"rule\"></div>'" in page
    assert "'<div class=\"pane\">' + detailOf(f) + '</div></div>'" in page
    assert ".panes { display: grid; grid-template-columns: 200px 1px" in page


def test_the_outline_grows_the_panel_instead_of_scrolling_inside_it(page):
    """A scroll region here would hide the end of a long flow inside a box that
    is already inside a box, and leave the page's own scrollbar pointing at
    nothing. A long flow makes the panel taller; that is what the page scrolls
    for."""
    flows = page.split("/* ---- flows ---")[1].split("/* ---- modals")[0]
    assert "overflow" not in flows
    assert "max-height" not in flows


def test_the_params_section_is_there_even_when_there_are_none(page):
    """Dropping the heading would teach the reader that flows have no
    parameters. Saying "this flow takes nothing" teaches them the section
    exists and that this one is empty."""
    assert "'<div class=\"olabel\">Params</div>'" in page
    assert "This flow takes nothing." in page


def test_the_flow_actions_are_in_the_panel_head_not_under_the_steps(page):
    """They act on the DOCUMENT, so they belong beside its name. Under the
    steps, a long flow pushed them off the screen and a Delete sitting at the
    bottom of a list of steps read as though it deleted a step."""
    head = page.split("'<div class=\"panel\">' +")[1].split("'</div></div>' +")[0]
    for attr in ("data-edit", "data-move", "data-drop"):
        assert attr in head, attr
    assert '<div class="acts">' in head
    # And nothing is left behind at the bottom.
    body = page.split("'<div class=\"panes\">")[1].split("function ")[0]
    assert "<button" not in body


def test_an_action_icon_still_carries_its_word(page):
    """A pencil, a globe and a trash with no labels are three mysteries. The
    word each one lost goes into `title` for a mouse and `aria-label` for a
    screen reader — it is not dropped, it is moved."""
    head = page.split("'<div class=\"panel\">' +")[1].split("'</div></div>' +")[0]
    assert head.count("aria-label=") == 3
    assert head.count("title=") == 3


def test_a_step_that_binds_a_secret_is_marked(page):
    """One argument, on one action. The mark keys off the argument existing
    rather than off a source name inside it (§F1.38)."""
    assert "const bindsSecret" in page
    assert "step.args && step.args.secret" in page


def test_a_parameter_reference_is_shown_as_written(page):
    """`${site}/login` is the argument. Seeing which arguments a parameter
    reaches is the point of reading a step, and unlike a secret there is
    nothing to hide — a parameter is non-secret by definition."""
    assert "const args = step.args || {};" in page
    assert "v.name + ' / ' + v.key" in page, "a secret still shows only its name"


def test_only_shared_flows_are_badged(page):
    """A badge on everything says nothing, so a session's own flows carry none.
    It sits at the END of the row rather than in front of the name, so the
    names line up down the column and stay the thing you scan."""
    assert "f.shared\n          ? '<span class=\"globe\" role=\"img\"" in page
    assert "'&#127760; ' + SF.esc(f.name)" not in page


def test_there_is_no_move_button_when_both_ends_are_the_same_folder(page):
    """`global` is a name a caller may legitimately choose, and such a session's
    library *is* the shared one — so the move's source and target are the same
    place. The server answers `moved: false`, correctly; offering the button at
    all dresses a no-op as an action."""
    assert "const move = !flowsData || flowsData.session !== 'global';" in page
    assert "(move\n      ? '<button type=\"button\" data-move" in page


def test_the_move_button_is_one_verb_whose_icon_is_the_destination(page):
    """There is no separate promote: a flow lives in exactly one directory, so
    the only action is which one (§F1.2). As an icon it has to show where the
    flow is GOING — a globe on a session flow sends it to global, a house on a
    shared one brings it back — because an icon of the current state would look
    like a badge rather than a button."""
    assert "const moveSaid = f.shared ? 'Move to this session' : 'Move to global';" in page
    assert "(f.shared ? '&#127968;' : '&#127760;')" in page
    # The word it no longer shows has to still be readable and announceable.
    assert "title=\"' + moveSaid + '\"" in page
    assert "aria-label=\"' + moveSaid + '\"" in page
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
