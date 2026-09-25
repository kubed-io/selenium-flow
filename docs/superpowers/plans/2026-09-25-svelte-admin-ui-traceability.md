# Traceability: every page-grepping test, mapped before the page moves

90 tests mapped, 30 retired (120 total); every inventory item (A1…C1,
including F9 and W9 added below) is covered — either by a row in this table
or, for behaviour no old test protected (sign-in, tabs, the live badge, the
console, R4/R5/R6), listed in **Coverage check: items with no originating
row**.

Scope, per the task brief: all 109 tests in `tests/test_admin_ui.py`; the
page-grepping tests in `tests/test_files_and_admin.py` from
`test_the_admin_page_needs_no_token` through
`test_the_components_render_no_action_buttons`, plus
`test_the_flow_panel_shows_a_selector_as_one_expression`; and the `static` row
of `tests/test_packaging.py::DATA_DIRS`.

**Excluded** (Python API, not page tests — unaffected by the Svelte rewrite):
in `test_files_and_admin.py`, everything from `test_the_admin_api_requires_the_token`
through `test_the_event_stream_signature_is_bound_to_its_own_path` — signed
links, auth, ending a session, the listing's shape, MCP resource/tool wiring,
CSP objects and event-stream signing all test the server's HTTP/MCP surface,
not the served page.

Two spec gaps found while mapping are added to the Behaviour inventory in the
same commit as this table: **F9** (Files carries no clear action) and **W9**
(the Flows tab has no accordion).

## `tests/test_admin_ui.py`

| Test | Protects (one line, in behaviour terms) | Becomes |
|---|---|---|
| test_files_and_flows_are_tabs | Files and Flows are tabs under a session | D3 → `SessionDetail.test.ts` "Files and Flows are tabs, routed by the hash" (Task 7) |
| test_the_files_tab_is_three_rows_in_order | Downloads, Screenshots, Files render in that order | F1 → `SessionDetail.test.ts` "switch-in: Loading… everywhere, then the three rows with counts (D4, F1, F2, F6)" (Task 7) — extend to assert section order; see new test list |
| test_each_row_that_clears_has_its_own_button | Clear downloads/screenshots are separate buttons; Files has none | F3, F4, F9 → `SessionDetail.test.ts` "Clear downloads lists every name with its fate, and clears (F3)" and "Clear screenshots lists them and says what stays (F4)" (Task 7); the F9 half needs a new test — see new test list |
| test_clear_downloads_needs_a_live_browser_not_an_attached_one | Clear downloads needs `live`, not `attached` | F3 → `SessionDetail.test.ts` "Clear downloads needs a live browser and this session's files (F3)" (Task 7) |
| test_the_tab_is_in_the_hash | The Files/Flows subtab and the session are both in the hash | D3, R2 → `SessionDetail.test.ts` "Files and Flows are tabs, routed by the hash (D3)" (Task 7); `router.test.ts` "parse (R2)" (Task 3) |
| test_clearing_screenshots_confirms_with_the_names | Clear screenshots asks first, through the modal, with a DELETE | F4 → `SessionDetail.test.ts` "Clear screenshots lists them and says what stays (F4)" (Task 7) |
| test_keeping_posts_to_the_folder_it_came_from | Keep POSTs to the folder the file is actually in | F5 → `SessionDetail.test.ts` "a tile keep posts to its own folder and reloads; a failure alerts (F5)" (Task 7) |
| test_a_section_can_be_opened_without_a_mouse | An accordion toggle is a real, focusable button announcing its state | F1 → `SessionDetail.test.ts` "switch-in: Loading… everywhere, then the three rows with counts (D4, F1, F2, F6)" (Task 7) |
| test_flows_has_no_accordion_left_to_toggle | The Flows tab has no accordion to collapse | W9 → `FlowsPane.test.ts` (Task 8) — new test; see new test list |
| test_the_file_action_is_a_button_rather_than_a_span_that_acts | A tile's action is a real button, not a span with a hand-rolled keydown | F5 → `FileGrid.test.ts` "keep: 📌 top-left, disabled for the round trip, re-armed on failure (F5)" and "delete: 🗑 hands the file to the page" (Task 4) |
| test_a_late_reply_cannot_render_one_session_under_another | A stale load cannot paint over the session now on screen | RETIRED: asserts `gone(key)` appearing exactly twice in `loadFiles`/`loadFlows` — the guarantee is D4 in `session.test.ts` "dispose: an answer after the session left never paints" (Task 7) |
| test_a_load_in_flight_disables_both_clear_buttons | Both clear buttons disable before the request goes out, not only on failure | F3 → `SessionDetail.test.ts` (Task 7) — new test; see new test list |
| test_a_browser_change_drops_the_stale_files_snapshot_before_showdetail | A browser change blanks Downloads and disables Clear screenshots before repaint | F8 → `session.test.ts` "a changed browser blanks Downloads, disarms the clears, and forces a reload (F8)" (Task 7) |
| test_a_late_flow_cannot_overwrite_the_one_you_just_picked | Picking flow B while A is still loading cannot land A's document under B's name | RETIRED: asserts a specific guard string appearing twice — the guarantee is generic in `latest.test.ts` "an overtaken load never paints, even when it answers last" (Task 3), applied to the doc loader in `session.svelte.ts` |
| test_the_last_page_gets_a_row_of_its_own_and_is_a_link | The last page is its own row, and a real link | D1 → `SessionSummary.test.ts` "named: no key or held-by; groups by lifetime (D1)" (Task 4) |
| test_a_last_page_that_is_not_a_web_url_is_not_linked | A non-http(s) URL is shown as text, never a clickable link | D1 → `format.test.ts` "safeHref only lets http(s) through" (Task 3); `SessionSummary.test.ts` "unnamed shows key and held-by; a javascript: page is text; nowhere yet" (Task 4) |
| test_the_header_no_longer_repeats_the_file_count | The header does not restate the file count Files already shows | RETIRED: a source-string absence check on `sessionSummary` — `SessionRow`/`SessionSummary` (Task 3/4) has no `files_count` field in its rendered groups at all; exhaustively asserted (not merely absent) by D1 in `SessionSummary.test.ts` "named: no key or held-by; groups by lifetime" |
| test_the_header_groups_the_two_lifetimes | The header groups session-lifetime facts apart from browser-lifetime facts | D1 → `SessionSummary.test.ts` "named: no key or held-by; groups by lifetime (D1)" (Task 4) |
| test_a_tile_carries_one_action_and_no_status_mark | A tile has one action (keep or delete), no separate status marks | F5 → `FileGrid.test.ts` "keep: 📌 top-left, disabled for the round trip, re-armed on failure (F5)" and "delete: 🗑 hands the file to the page" (Task 4) |
| test_the_lightbox_steps_and_says_where_it_is | Prev/Next, arrow keys, and "i / n" | X1 → `Lightbox.test.ts` "steps through the row it was opened on, stopping at the ends (X1, X2)" (Task 4) |
| test_the_lightbox_disables_its_ends_rather_than_wrapping | Prev/Next disable at the ends instead of wrapping | X1 → `Lightbox.test.ts` "steps through the row it was opened on, stopping at the ends (X1, X2)" (Task 4) |
| test_a_cancelled_lightbox_action_does_not_alert | A cancelled confirm inside the lightbox is silent, not alerted | X3 → `Lightbox.test.ts` "an empty refresh closes; cancelled is silent; any other failure alerts (X3)" (Task 4) |
| test_keep_in_the_lightbox_moves_on_to_the_next_screenshot | Keep in the lightbox moves on without closing it | X3 → `SessionDetail.test.ts` "the lightbox Keep moves on to the next screenshot (X3)" (Task 7) |
| test_the_lightbox_refresh_does_not_reload_a_session_the_operator_has_left | The lightbox's own refresh must not reload a session the operator left | RETIRED: asserts `gone(key)` ordering/count in the refresh closure — the guarantee is D4 in `session.test.ts` "dispose: an answer after the session left never paints" (Task 7) |
| test_the_lightbox_refresh_waits_for_the_newest_load | The lightbox refresh waits for the newest load, not the one that started it (the Keep race) | X3 → `latest.test.ts` "settled() waits for the newest load, not the one it was handed (the Keep race)" (Task 3); `session.test.ts` "filesSettled sees the newest data even when the poll overtook the caller (X3, the Keep race)" (Task 7) |
| test_open_lightbox_reads_filesdata_directly | The lightbox reads straight from the files data, with no dead `'kept'` mapping | RETIRED: implementation detail (a since-removed string mapping) — the `Folder` type (`types.ts`, Task 3) is `'downloads' \| 'screenshots' \| 'files'` and nothing in `FilesPane.svelte`/`SessionDetail.svelte` (Task 7) ever produces `'kept'` |
| test_the_lightbox_returns_a_handle_to_close_it | Something outside the render layer can close the lightbox | RETIRED: implementation detail (`return {close}`) — the lightbox is now `lightbox = $state<{folder,index}\|null>` owned by `SessionDetail.svelte`; dismissing it from outside is `lightbox = null`, exercised by D4/F8 in `SessionDetail.test.ts` "a browser change closes the Downloads lightbox and its confirm" and "the session going away takes its overlays and cancels its modal" (Task 7) |
| test_a_session_change_closes_the_open_lightbox | Switching sessions closes any lightbox left open | D4 → `SessionDetail.test.ts` "the session going away takes its overlays and cancels its modal (D4, M1)" (Task 7) |
| test_the_lightbox_action_refuses_a_session_that_has_moved_on | A lightbox action started just as the session went away is refused, not run | D4 → `SessionDetail.test.ts` (Task 7) — new test; see new test list |
| test_the_lightbox_ignores_keys_while_a_modal_is_open_above_it | Esc/arrow keys are ignored while a confirm sits on top of the lightbox | X2 → `Lightbox.test.ts` "Esc, Close and the backdrop close it; keys are ignored under a modal (X2)" (Task 4) |
| test_the_app_draws_three_read_only_rows | The MCP App renders the three file rows, read-only | P1 → `FileSections.test.ts` "three titled rows with counts, in order, read-only (P1)" (Task 4) |
| test_the_session_card_names_each_count | A session card names each folder's count in words, not just a number | L3 → `format.test.ts` "countsText says each folder in its own word, and falls back to files_count" (Task 3) |
| test_the_shared_library_renders_the_action_but_never_wires_it | The shared components render an action's markup but never wire a handler | P1 → `FileGrid.test.ts` "no action, no button — the MCP App holds no credential" (Task 4) |
| test_clearing_downloads_lists_the_names_it_will_remove | Clear downloads' confirm lists the names it will remove | F3 → `SessionDetail.test.ts` "Clear downloads lists every name with its fate, and clears (F3)" (Task 7) |
| test_the_clear_confirm_names_every_download_the_grid_holds | The confirm names every download, not a filtered subset | F3 → `SessionDetail.test.ts` "Clear downloads lists every name with its fate, and clears (F3)" (Task 7) |
| test_clearing_says_what_happens_to_each_name | Each name's fate (gone, or a copy stays in Files) is said per row | F3 → `SessionDetail.test.ts` "Clear downloads lists every name with its fate, and clears (F3)" (Task 7) |
| test_the_kept_names_come_from_files_itself | The "stays" set comes from the Files listing itself, not a flag | F3 → `SessionDetail.test.ts` "Clear downloads lists every name with its fate, and clears (F3)" (Task 7) |
| test_an_outline_row_is_an_icon_and_a_name | An outline row is a glyph and a name; arguments never show there | W3 → `FlowsPane.test.ts` "the outline: params first with type glyph and required star; steps with tool glyph, id and lock (W3)" (Task 8) |
| test_the_outline_does_not_number_its_rows | The outline is unnumbered; only "used by" citations number their rows | W3 → `FlowsPane.test.ts` (Task 8) — new test; see new test list |
| test_required_is_a_star_on_a_parameter_and_nowhere_else | Required is a star on a parameter; steps carry no such mark | W3 → `FlowsPane.test.ts` "the outline: params first with type glyph and required star; steps with tool glyph, id and lock (W3)" (Task 8) |
| test_a_parameters_glyph_is_its_type | A parameter's glyph is its JSON type | W3 → `FlowsPane.test.ts` "the outline: params first with type glyph and required star; steps with tool glyph, id and lock (W3)" (Task 8), for `string`; full enumeration needs a new test — see new test list |
| test_every_runnable_action_has_a_glyph | Every runnable action (`flows.run.RUNNABLE`) has a glyph | W3 → needs a new, cross-language test (Python `RUNNABLE` vs `ui/src/admin/flow.ts`'s `TOOL_ICON`) with no assigned task file — see new test list and report |
| test_a_row_still_says_its_tool_to_a_screen_reader | The tool name survives for a screen reader, as `role="img"`/`title` | W3 → `FlowsPane.test.ts` "the outline: params first with type glyph and required star; steps with tool glyph, id and lock (W3)" (Task 8) |
| test_the_detail_sits_beside_the_outline_rather_than_under_it | The three-column layout: outline, rule, detail | W3 → Visual parity (before/after screenshots, Task 1 and Task 13) — CSS layout is not grep-testable and the spec's Testing section delegates it to screenshots, not a DOM assertion |
| test_the_outline_grows_the_panel_instead_of_scrolling_inside_it | The outline grows the panel instead of scrolling inside a box | W3 → Visual parity (before/after screenshots, Task 1 and Task 13) |
| test_the_params_section_is_there_even_when_there_are_none | Params section always shows, even "This flow takes nothing." | W3 → `FlowsPane.test.ts` "a malformed flow never breaks the panel (W8)" (Task 8) — its `parameters: 1` case renders "This flow takes nothing." |
| test_the_flow_actions_are_in_the_panel_head_not_under_the_steps | Edit/Move/Delete sit in the panel head, not under the steps | W3 → `FlowsPane.test.ts` "Edit reads the file again, shows it raw, and saves it (W4)" and "Move and Delete say where, act, and close the flow (W5, W6)" (Task 8); placement itself is Visual parity (Task 1/13) |
| test_an_action_icon_still_carries_its_word | Edit/Move/Delete icons keep their word in `title`/`aria-label` | W3 → `FlowsPane.test.ts` "Move and Delete say where, act, and close the flow (W5, W6)" (Task 8) |
| test_a_document_lookup_cannot_find_what_object_gave_it | A lookup keyed by document content (`tool: constructor`) cannot hit `Object.prototype` | W8 → `flow.test.ts` "own never finds what Object gave the map (W8)" (Task 3) |
| test_a_listing_refresh_carries_the_open_flow_with_it | A listing refresh reloads the open flow, or closes it if it vanished | W7 → `session.test.ts` "an open flow that vanished from the listing is closed (W7)" and "a still-listed open flow reloads its document (W7)" (Task 7) |
| test_a_refresh_is_not_a_click | A heartbeat reuses the fetch without resetting the picked selection | W2 → `FlowsPane.test.ts` (Task 8) — new test; see new test list |
| test_a_document_fetch_has_its_own_generation | An older flow-doc fetch cannot land after, and stick over, a newer one | RETIRED: asserts `flowDocSeq`/`mine !== flowDocSeq` by name and count — the guarantee is generic in `latest.test.ts` "an overtaken load never paints, even when it answers last" (Task 3), applied to `#docLoads` |
| test_a_malformed_ENTRY_does_not_take_the_panel_with_it | A `[null]` step entry never breaks the outline | W8 → `FlowsPane.test.ts` "the outline: params first with type glyph and required star; steps with tool glyph, id and lock (W3)" (Task 8) — its DOC includes a `null` step rendered as ❓ |
| test_a_thing_that_is_present_but_empty_is_not_reported_as_gone | A declared-but-empty parameter/step is not reported as "gone" | W8 → `FlowsPane.test.ts` (Task 8) — new test; see new test list |
| test_a_malformed_document_does_not_take_the_panel_with_it | `steps: {}`, `parameters: 1` never break the panel | W8 → `FlowsPane.test.ts` "a malformed flow never breaks the panel (W8)" (Task 8) |
| test_a_step_that_binds_a_secret_is_marked | A step that types a secret is marked, keyed off the argument existing | W3 → `flow.test.ts` "bindsSecret" (Task 3); `FlowsPane.test.ts` "the outline… (W3)" (Task 8) shows the 🔒 |
| test_a_parameter_reference_is_shown_as_written | A parameter reference in an argument is shown as written, unlike a secret | W3 → `flow.test.ts` "argValue: a secret by reference, a selector unwrapped, objects as JSON" (Task 3); `FlowsPane.test.ts` "step detail: number, chip, tool, arguments, code, selector, secret, behaviour (W3)" (Task 8) |
| test_only_shared_flows_are_badged | Only a shared flow gets the globe badge, at the end of the row | W1 → `FlowsPane.test.ts` "the list: names, step counts, a globe only when shared, the open one selected (W1)" (Task 8) |
| test_there_is_no_move_button_when_both_ends_are_the_same_folder | No Move button when the session is `global` (source == target) | W5 → `FlowsPane.test.ts` (Task 8) — new test; see new test list |
| test_the_move_button_is_one_verb_whose_icon_is_the_destination | Move is one verb; its icon shows where the flow is going | W5 → `FlowsPane.test.ts` "Move and Delete say where, act, and close the flow (W5, W6)" (Task 8) |
| test_deleting_a_flow_warns_when_it_is_the_shared_one | Delete's wording covers both a session flow and the shared one | W6 → `FlowsPane.test.ts` "Move and Delete say where, act, and close the flow (W5, W6)" (Task 8) |
| test_the_editor_is_handed_the_stored_yaml | The editor holds the raw YAML, and Save PUTs the raw text back | W4 → `FlowsPane.test.ts` "Edit reads the file again, shows it raw, and saves it (W4)" (Task 8) |
| test_the_editor_reads_the_file_again_when_it_opens | Edit refetches the file before showing the editor | W4 → `FlowsPane.test.ts` "Edit reads the file again, shows it raw, and saves it (W4)" (Task 8) |
| test_flows_being_off_reads_as_off_rather_than_broken | "Flows are off…" reads as configuration, not a fault | W1 → `FlowsPane.test.ts` "flows off, and none yet (W1)" (Task 8) |
| test_the_page_surfaces_the_servers_own_message | A failed call shows the server's own `error` text, or a status fallback | A5 → `api.test.ts` "a failure says the server's own words, else the status (A5)" (Task 3) |
| test_the_flows_panel_repaints_when_the_flows_change | A pushed `flows_rev` change repaints the Flows panel, not just Files | F8/W7 → `session.test.ts` (Task 7) — new test paired with "a pushed row refetches files only when the stamp moves (F8)"; see new test list |
| test_neither_stamp_is_a_count | Both panels watch a revision token, not a count | RETIRED: asserts the strings `row.files_rev`/`row.flows_rev` appear — the guarantee is exercised behaviourally by F8 in `session.test.ts` "a pushed row refetches files only when the stamp moves" (Task 7), and by its flows_rev counterpart (new test, see list) |
| test_the_flows_stamp_is_not_a_count | The flows heartbeat watches `flows_rev`, not the step/flow count | RETIRED: asserts `flowsStamp`/`shownFlows` by name — same guarantee as above, F8/W7 in `session.test.ts` (Task 7) plus its new flows_rev test |
| test_opening_a_session_forgets_what_the_last_one_showed | A newly opened session starts from a clean slate, not the last one's state | RETIRED: asserts one variable-reset line by name — structurally guaranteed in the new design: `Admin.svelte`'s `{#key route.key}` (Task 7 intro) destroys the whole `SessionDetail`/`SessionModel` subtree on every switch, so a fresh model is constructed each time (`session.test.ts`, Task 7) |
| test_two_loads_of_the_same_panel_cannot_race_each_other | The last request issued is the only one allowed to render, for both panels | RETIRED: asserts `filesSeq`/`flowsSeq` counters by name and a count of 2 — the guarantee is generic in `latest.test.ts` "an overtaken load never paints, even when it answers last" (Task 3), applied per-panel via `#fileLoads`/`#flowLoads` |
| test_the_page_script_is_valid_javascript | The served script must actually parse | RETIRED: a `node --check` floor under string-matching tests that have no DOM — superseded by the build itself: `npm --prefix ui run build`/`run check` (svelte-check, TypeScript), wired into CI in Task 11, fails loudly on anything that does not compile |
| test_a_multi_line_argument_is_shown_as_code | A multi-line argument value renders as a code block, keyed on content | W3 → `FlowsPane.test.ts` "step detail: number, chip, tool, arguments, code, selector, secret, behaviour (W3)" (Task 8) |
| test_the_session_a_flow_fetch_is_for_is_passed_not_read | A flow reload is threaded the session key, never re-reads a shared "current" | RETIRED: asserts an absent source pattern and that `openFlow(` is absent from the save path — structurally guaranteed: `SessionModel` (Task 7) is instantiated per session key and closes over it; there is no shared `current` to misread |
| test_acting_on_one_session_does_not_disturb_another | An action on session A cannot touch session B's selection or reload | RETIRED: asserts guard-count patterns in a page-wide event handler — each session now owns its own `SessionModel` instance (Task 7), demonstrated directly by `session.test.ts` "a pushed row for another session, or none, changes nothing" |
| test_opening_a_session_is_guarded_after_each_of_its_own_awaits | Moving to a third session mid-load cannot let the second session's resumption strand it | RETIRED: asserts `gone(key)` appears exactly twice around specific awaits in `openSession` — no such function exists; `SessionDetail`'s `onMount` (Task 7) checks `destroyed` after each await, exercised by D4 in `session.test.ts` "dispose: an answer after the session left never paints" |
| test_a_deep_linked_flow_is_checked_against_the_listing_first | A deep-linked flow only opens if the listing actually contains it | D5 → `FlowsPane.test.ts` (Task 8) — new test; see new test list |
| test_switching_sessions_disarms_the_clear_buttons_until_the_new_one_answers | A freshly opened session starts with both clear buttons disarmed | D4, F3 → `session.test.ts` "a load paints the rows and the header (F2, D1)" (Task 7) — `m.view` is `null` and `m.files` is `NO_FILES` before the first load resolves, which is what disarms both buttons |
| test_a_failed_file_load_disarms_the_clear_buttons_too | A failed files load also disarms both clear buttons | F7 → `session.test.ts` "a failed load leaves nothing actionable behind (F7)" (Task 7) |
| test_a_kept_or_deleted_file_only_reloads_its_own_session | Acting on a file only reloads the session it belongs to | RETIRED: asserts `if (current === key) loadFiles(key);` appears exactly twice — no shared `current` exists; each `keep`/`delete` closes over its own session's `m` and is guarded by `destroyed` (Task 7), covered by D4 in `SessionDetail.test.ts` "the session going away takes its overlays and cancels its modal" |
| test_clearing_either_folder_only_reloads_its_own_session | Clearing a folder only reloads the session it belongs to | RETIRED: same pattern as above — `SessionDetail.test.ts` "the session going away takes its overlays and cancels its modal (D4, M1)" (Task 7) |
| test_only_downloads_go_with_the_browser | Only Downloads is blanked on a browser change; Screenshots/Files are not | F8 → `session.test.ts` "a changed browser blanks Downloads, disarms the clears, and forces a reload (F8)" (Task 7) |
| test_a_closed_flow_takes_its_hash_with_it | Closing a flow (move/delete/vanish) resets the hash to `…/flows` | W6/W7 → `FlowsPane.test.ts` "Move and Delete say where, act, and close the flow (W5, W6)" (Task 8) checks the hash after Move; the vanish path is `session.test.ts` "an open flow that vanished from the listing is closed (W7)" wired to `replace(hashes.flows(key))` (Task 7) |
| test_clearing_one_screenshot_does_not_say_all | Clearing the one and only screenshot reads "the 1 screenshot," not "all 1" | F4 → `SessionDetail.test.ts` (Task 7) — new test; see new test list |
| test_the_screenshot_clear_comment_matches_what_it_lists | A stale source comment must not contradict what the confirm lists | RETIRED: checks a comment string in the old source — the actual behaviour (every name listed) is F4 in `SessionDetail.test.ts` "Clear screenshots lists them and says what stays" (Task 7) |
| test_secrets_sit_beside_sessions | Secrets is a top tab beside Sessions and Console | R1 → `Admin.test.ts` (Task 6) — new test; see new test list |
| test_a_secret_card_links_to_the_flow_that_types_it | A secret's card links to the (non-shared) flow that types it | S1 → `SecretsPane.test.ts` "Loading…, then a card per secret with keys, allowed, source and backlinks (S1)" (Task 9) |
| test_the_secrets_page_never_renders_a_value | The Secrets page never renders a secret's value | RETIRED: a source-string absence check (`.value`) — structurally guaranteed: `Secret` (`types.ts`, Task 3) has no `value` field, and `SecretsPane.svelte`/`SecretsPane.test.ts` (Task 9) never receive or render one |
| test_a_name_no_secret_answers_to_is_shown | A flow naming an undefined secret is shown, under its own heading | S1 → `SecretsPane.test.ts` "Loading…, then a card per secret with keys, allowed, source and backlinks (S1)" (Task 9) |
| test_leaving_for_secrets_drops_current | Navigating to Secrets drops the open session's detail view | RETIRED: asserts an explicit `current = null` assignment — superseded structurally: `Admin.svelte` (Task 9, Step 3) conditionally renders `SecretsPane` in place of `SessionDetail`, which unmounts it; the plan notes "no `current = null` needed" |
| test_clear_downloads_stays_off_while_filesdata_is_stale | Clear downloads stays off while the files snapshot is stale, even if `live` | F3 → `SessionDetail.test.ts` "Clear downloads needs a live browser and this session's files (F3)" (Task 7) — `clearDownloadsOff` derives from `m.files === NO_FILES` as well as `!m.row.live` |
| test_a_successful_load_rearms_clear_downloads_from_filesdata | A successful load re-evaluates and can re-arm Clear downloads | F3 → `SessionDetail.test.ts` "Clear downloads lists every name with its fate, and clears (F3)" (Task 7) |
| test_opening_a_new_session_blanks_the_count_pills | Opening a session blanks the count pills until the load answers | D4 → `SessionDetail.test.ts` "switch-in: Loading… everywhere, then the three rows with counts (D4, F1, F2, F6)" (Task 7) |
| test_a_failed_file_load_blanks_the_count_pills_too | A failed files load blanks the count pills too | F7 → `session.test.ts` "a failed load leaves nothing actionable behind (F7)" (Task 7) |
| test_the_keep_tile_is_disabled_for_its_own_round_trip | A tile's Keep button disables for its own round trip | F5 → `FileGrid.test.ts` "keep: 📌 top-left, disabled for the round trip, re-armed on failure (F5)" (Task 4) |
| test_modal_exposes_a_cancel_path_a_session_switch_can_drive | The modal exposes a real cancel path, not a bare removal | RETIRED: asserts an `activeModal`/`box.close` internal structure that no longer exists — the cancel path is M1 in `Modal.test.ts` "Esc, the backdrop and Cancel all take the cancel path" (Task 6) and D4/M1 in `SessionDetail.test.ts` "the session going away takes its overlays and cancels its modal" (Task 7) |
| test_a_session_change_also_cancels_any_open_modal | Switching sessions cancels any modal left open, not only the lightbox | D4, M1 → `SessionDetail.test.ts` "the session going away takes its overlays and cancels its modal (D4, M1)" (Task 7) |
| test_every_onconfirm_that_acts_on_a_captured_key_rechecks_it_first | Every confirm that acts on a captured session key rechecks it is not gone | D4 → `SessionDetail.test.ts` (Task 7) — new test; see new test list (`refuseIfGone`'s message, tested once rather than per call site) |
| test_the_lightbox_deletes_onconfirm_rechecks_the_session | The lightbox's own Delete rechecks the session before it runs | RETIRED: one of seven identical per-call-site checks — the mechanism is `refuseIfGone()` (`SessionDetail.svelte`, Task 7), covered once by the new test above |
| test_the_tile_deletes_onconfirm_rechecks_the_session | A tile's own Delete rechecks the session before it runs | RETIRED: same pattern; covered by the new `refuseIfGone` test (Task 7) |
| test_the_flow_editors_save_rechecks_the_session | The flow editor's Save rechecks the session before it runs | RETIRED: same pattern; covered by W4 in `FlowsPane.test.ts` "Edit reads the file again, shows it raw, and saves it" (Task 8) and the new `refuseIfGone` test (Task 7) |
| test_moving_a_flow_rechecks_the_session | Move rechecks the session before it runs | RETIRED: same pattern; covered by W5 in `FlowsPane.test.ts` "Move and Delete say where, act, and close the flow" (Task 8) and the new `refuseIfGone` test (Task 7) |
| test_deleting_a_flow_rechecks_the_session | Delete rechecks the session before it runs | RETIRED: same pattern; covered by W6 in `FlowsPane.test.ts` "Move and Delete say where, act, and close the flow" (Task 8) and the new `refuseIfGone` test (Task 7) |
| test_clear_downloads_rechecks_the_session | Clear downloads rechecks the session before it runs | RETIRED: same pattern; covered by F3 in `SessionDetail.test.ts` "Clear downloads lists every name with its fate, and clears" (Task 7) and the new `refuseIfGone` test |
| test_clear_screenshots_rechecks_the_session | Clear screenshots rechecks the session before it runs | RETIRED: same pattern; covered by F4 in `SessionDetail.test.ts` "Clear screenshots lists them and says what stays" (Task 7) and the new `refuseIfGone` test |
| test_refresh_detail_treats_a_liveness_flip_like_a_browser_change | A `live` flip alone (same browser id) is treated like a browser change | F8 → `session.test.ts` "a changed browser blanks Downloads, disarms the clears, and forces a reload (F8)" (Task 7) — its fixture flips `live: true → false` with `session_id` unchanged |
| test_a_stale_clear_downloads_confirm_is_closed_when_the_browser_changes | A Clear-downloads confirm scoped to the old browser is closed on a browser change | F8, M1 → `SessionDetail.test.ts` "a browser change closes the Downloads lightbox and its confirm (F8)" (Task 7) |
| test_a_stale_downloads_lightbox_is_closed_before_the_browser_changes | A Downloads lightbox is closed before the browser's files are dropped | F8 → `SessionDetail.test.ts` "a browser change closes the Downloads lightbox and its confirm (F8)" (Task 7) |
| test_ending_the_browser_closes_its_downloads_overlays | End browser also closes any open Downloads lightbox/confirm | D2 → `SessionDetail.test.ts` (Task 7) — new test; see new test list |
| test_a_browser_change_forces_a_files_reload | A browser change always forces a Files reload, even at an unchanged stamp | F8 → `session.test.ts` "a changed browser blanks Downloads, disarms the clears, and forces a reload (F8)" (Task 7) |

## `tests/test_files_and_admin.py` (page-grepping subset)

| Test | Protects (one line, in behaviour terms) | Becomes |
|---|---|---|
| test_the_admin_page_needs_no_token | The admin shell loads with no token; auth happens per-call, not per-page | A2/A3-adjacent → `test_ui_serving.py::test_without_a_build_the_page_is_the_placeholder` and `::test_with_a_build_the_page_inlines_its_bundle_and_fills_the_server_values` (Task 10) both `GET /` with no `Authorization` header and get 200 |
| test_the_admin_page_carries_the_shared_components | The dashboard and the MCP App render from one shared component library | RETIRED: a source-string check (`const SF`, `SF.fileGrid`, `--accent`) — structurally guaranteed: both surfaces import the same `ui/src/lib/*` components (Task 4), exercised by `FileSections.test.ts`/`App.test.ts` (Task 5) and `FileGrid`'s use in `SessionDetail.test.ts` (Task 7) |
| test_both_destructive_actions_ask_first_and_report_a_failure | Both destructive actions (End browser, Clear downloads) ask first and report a failure | D2, F3 → `SessionDetail.test.ts` "End browser asks, deletes, and reloads; disabled when not attached (D2)" and "Clear downloads lists every name with its fate, and clears (F3)" (Task 7) |
| test_neither_toolbar_action_is_offered_without_a_browser | End browser needs `attached`; Clear downloads needs the stricter `live` | D2, F3 → `SessionDetail.test.ts` "End browser asks, deletes, and reloads; disabled when not attached (D2)" and "Clear downloads needs a live browser and this session's files (F3)" (Task 7) |
| test_the_detail_view_is_updated_by_the_event_stream | A push while the detail view is open repaints it in place, not on navigation | U2, F8 → `SessionDetail.test.ts` "a browser change closes the Downloads lightbox and its confirm (F8)" (Task 7) — drives the update by mutating `live.data`, exercising the `$effect` that calls `onPushed` while mounted |
| test_a_changed_browser_clears_the_file_grid | A browser change clears Downloads; Screenshots/Files are untouched | F8 → `session.test.ts` "a changed browser blanks Downloads, disarms the clears, and forces a reload (F8)" (Task 7) |
| test_the_file_grid_is_not_redrawn_on_every_heartbeat | The file grid is not redrawn on every heartbeat, only on a real change | F8 → `session.test.ts` "a pushed row refetches files only when the stamp moves (F8)" (Task 7) |
| test_a_dead_event_stream_is_reopened_with_a_fresh_url | A stalled stream reopens on a freshly signed URL, not the expired one | U3 → `live.test.ts` "a silent stream falls back to polling and reopens on a fresh URL (U3)" (Task 6) |
| test_the_components_render_no_action_buttons | The shared components never render an action button on their own | P1 → `FileGrid.test.ts` "no action, no button — the MCP App holds no credential" (Task 4) |
| test_the_flow_panel_shows_a_selector_as_one_expression | A selector object (`{css: …}`) is shown as one expression, not raw JSON | W3 → `flow.test.ts` "a selector reads as one expression (W3)" and "argValue: a secret by reference, a selector unwrapped, objects as JSON" (Task 3) |

## `tests/test_packaging.py`

| Test | Protects (one line, in behaviour terms) | Becomes |
|---|---|---|
| test_a_data_directory_is_packaged_beside_the_module_that_reads_it[kubed/selenium_flow/http/admin.py-static] | The `static/` directory ships beside `admin.py` in the wheel | RETIRED: the mapped-package model is replaced entirely (Task 10, Step 6/7) — `static/` is now a built, gitignored output collected by glob (`package-data`), not a mapped package; the successor is `test_the_built_ui_ships_by_glob_so_an_unbuilt_install_still_works` in `tests/test_packaging.py` (Task 10) |

## New tests the plan must add

Behaviour the table above needs, that no test in Tasks 2–13 currently names:

- `SessionDetail.test.ts` (Task 7): the three Files sections render in document
  order — Downloads, Screenshots, Files (F1).
- `SessionDetail.test.ts` (Task 7): the kept ("Files") section offers no clear
  action of its own (F9, new inventory item).
- `FlowsPane.test.ts` (Task 8): the Flows tab has no accordion — no
  `[data-toggle]`/section element to open or close (W9, new inventory item).
- `SessionDetail.test.ts` (Task 7): both `#clearDownloads` and
  `#clearScreenshots` disable the instant a files load starts, before the
  request settles, not only after a failure (F3).
- `flow.test.ts` (Task 3): `TYPE_ICON` (or `own(TYPE_ICON, ty)`) has a glyph
  for every JSON type — `string`, `number`, `integer`, `boolean`, `object`,
  `array` (W3).
- A cross-language test with no assigned task file — `kubed.selenium_flow.flows.run.RUNNABLE`
  vs `ui/src/admin/flow.ts`'s `TOOL_ICON`: every runnable tool has a real
  glyph (W3). This cannot be a `vitest` file since it must import Python; it
  is likely a small Python test reading the TS source as text, the same way
  the retiring test did. Flagged for the controller — see report.
- `FlowsPane.test.ts` (Task 8): the outline never numbers its own rows; only
  a parameter's "used by" citation rows do (W3).
- `FlowsPane.test.ts` (Task 8): a parameter declared with an empty spec
  (`term:` with nothing after it) and a step entry that is an empty object
  are both shown, not reported "gone" — distinct from an absent one (W8).
- `FlowsPane.test.ts` (Task 8): a heartbeat-driven flow-doc reload does not
  reset the currently picked step or parameter, and shows no `Loading…`
  flash (W2, "a refresh is not a click").
- `FlowsPane.test.ts` (Task 8): there is no Move button when the open
  session is `global` (source and destination would be the same folder)
  (W5).
- `FlowsPane.test.ts` (Task 8): a deep-linked flow name absent from the
  listing is never fetched or opened (D5).
- `FlowsPane.test.ts` (Task 8): picking a flow in the list clears the current
  selection, shows `Loading…`, and moves the hash by `replaceState` (not a
  new history entry) before the document loads (W2).
- `SessionDetail.test.ts` (Task 7): clearing the one and only screenshot
  reads "the 1 screenshot," not "all 1 screenshots" (F4).
- `SessionDetail.test.ts` (Task 7): ending the browser also closes an open
  Downloads lightbox and any Downloads-scoped confirm, before reloading
  (D2).
- `session.test.ts` (Task 7): a pushed `flows_rev` change refetches flows
  the same way a pushed `files_rev` change refetches files, and an
  unchanged `flows_rev` does not (F8/W7 — the flows-side twin of "a pushed
  row refetches files only when the stamp moves").
- `Admin.test.ts` (Task 6): the top tab bar orders Sessions, Secrets,
  Console left to right, and `#paneSecrets` sits beside `#paneSessions`
  (R1).
- `Admin.test.ts` (Task 6): landing directly on `#/sessions/<key>` (a deep
  link, not the list) still opens the live stream (R5) — no old test
  protected this; added for coverage.
- `SessionDetail.test.ts` (Task 7): "← Sessions" navigates to `#/` (R6) — no
  old test protected this; added for coverage.
- `SessionDetail.test.ts` (Task 7): a keep/delete/move/save/clear started
  just before the session is left is refused with "that session is no
  longer on screen," not silently run or silently dropped (D4,
  `refuseIfGone`) — replaces seven near-identical per-call-site tests in the
  old suite with one general one.

## Coverage check: items with no originating row

These inventory items describe behaviour (sign-in, routing, the live badge,
the console, and a few edge cases) that no test in `test_admin_ui.py` or
`test_files_and_admin.py` ever grepped for — the client-side auth flow, tab
bar and console were previously only exercised by hand or by the integration
flows. They are covered going forward by:

- A1, A2 → `Admin.test.ts` "signed out: the sign-in card, and no Sign out
  (A1, A2)" and "a refused token says so; an accepted one is kept in
  sessionStorage only (A2)" (Task 6)
- A3 → `Admin.test.ts` "a stored token is probed; a dead one lands on
  sign-in (A3)" (Task 6)
- A4 → `api.test.ts` "a 401 signs out and fails the call (A4)" (Task 3);
  `live.test.ts` "stop closes the stream and the poll (A4)"; `Admin.test.ts`
  "Sign out stops the stream and forgets the token (A4)" (Task 6)
- A6 → `Admin.test.ts` "BASE is the page path and server URLs resolve
  against ROOT (A6)" (Task 6)
- R3 → `Admin.test.ts` "the console tab hides itself when it would frame
  this page (R3, C1)" (Task 6)
- R4 → Task 9, Step 3: leaving Secrets for a session unmounts
  `SessionDetail` by construction (conditional rendering); no dedicated test
- R5, R6 → new tests, see the list above
- U1, U2 → `live.test.ts` "loads, then streams from the signed URL against
  ROOT (U1, U2)" (Task 6)
- L1 → `Admin.test.ts` "three top tabs, one pane at a time; the live badge
  (R1, L1)" (Task 6)
- L2 → `live.test.ts` "an error shows in place of the list (L2)" (Task 6)
- C1 → `Admin.test.ts` "the console tab hides itself when it would frame
  this page (R3, C1)" (Task 6)
