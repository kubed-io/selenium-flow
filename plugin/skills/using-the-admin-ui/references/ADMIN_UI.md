# The admin page

The admin page is selenium-flow's own web page for people. It lists the
workspaces and opens one to watch its browser, tidy its files, flows and site
data, and edit its config. It is at the server's root, under `ROUTE_PREFIX`
when one is set, such as `https://flows.example.com/`; `/admin` redirects there.
Only a server reached over HTTP has one: a server on stdio has no admin page.

Signing in is ADMIN_SIGN_IN.md. An agent doing any of this in its own browser
reads AGENT_AS_ADMIN.md first.

## The top tabs

| Tab | Shows |
|---|---|
| Workspaces | a card per workspace, most recently used first: its browser, state, files, last page and sub-sessions; a click opens it |
| Secrets | each secret's name, keys, allowed origins and why one is refused; never a value, and nothing here creates or edits a secret |
| Settings | every server setting with its value and where it came from; read-only, since settings belong to the deployment |
| Grid console | the Grid's own console, when the Grid has one apart from this page |

## A workspace's tabs

The session tabs show the session picked in the chips above them, once the
workspace has a sub-session; the workspace tabs are the workspace's whatever is
picked.

| Tab | For | Scope |
|---|---|---|
| Session | what the session asked for, what the Grid gave, every capability the last open sent | session |
| Live | the browser's screen, to watch or take over (LIVE_VIEW.md) | session |
| Console, Network | the page's console and requests; disabled unless the browser was opened with capture | session |
| Site data | what is saved per host, to forget one or clear it all | session |
| Files | Downloads, Screenshots, Recordings and Files, to keep, delete or clear | workspace |
| Flows | the workspace's flows and the shared ones, to edit, tag, move or delete | workspace |
| History | where the workspace has been, to clear | workspace |
| Config | the workspace's `workspace.yaml` (WORKSPACE_CONFIG.md) | workspace |

## What changes something, and what it asks

A destructive action opens one confirm box. The box names what goes and what
stays, and its button is the verb, often with a count (`Clear 3 files`,
`Forget`, `Move`, `Delete`). Cancel, Escape and a click outside it cancel. A
failed action leaves the box open with the error in an alert.

| Action | Where | Asks with | Does |
|---|---|---|---|
| End browser, End *label* | the workspace's header, or the chips row with sub-sessions | the browser's own `confirm` dialog | ends that session's browser; its workspace, page and site data stay |
| End all | the chips row, with sub-sessions | a box listing each session | ends every session's browser and keeps every record |
| Keep | Files, on a screenshot, recording or download | nothing | moves or copies it into Files |
| Clear downloads | Files, per session, only while its browser runs | a box listing each download as gone, or its copy in Files as staying | empties that browser's downloads |
| Clear screenshots, Clear recordings | Files | a box with the count; kept files stay | deletes that folder's files |
| Delete a file | Files | a box saying it has no undo | deletes one file; Files is never cleared whole |
| Forget | Site data, on one host | a box of what goes and what stays | drops that host's saved cookies and storage |
| Clear | Site data | the same | empties the session's saved site data; the history stays |
| Clear | History | the same | keeps only each session's last page; site data stays |
| Edit (✏️) | Flows, an open flow | an editor holding the file as written | Save writes the YAML, comments kept |
| Move to global (🌐), Move to this workspace (🏠) | Flows, an open flow | a box naming the library it moves to | below |
| Delete | Flows, an open flow | a box; a global flow goes for every workspace | deletes the flow file |
| Edit YAML, Remove config | Config | Remove asks | WORKSPACE_CONFIG.md |

Saved site data that is forgotten or cleared leaves the live browser as it is:
only the next browser comes back without it.

## Flows

Moving is how a flow gets into the shared library, `global`, and out of it. No
agent and no tool can: `save_flow` and `delete_flow` refuse `global`.

1. Open the workspace that holds the flow, then its Flows tab.
2. Pick the flow. Its panel has three buttons: edit, move and delete.
3. Click 🌐, *Move to global*. The box says every workspace can then list and
   run it, and warns when the flow has no `sites` or `paths`, since then
   `flows_here` never names it.
4. Click **Move**. The flow closes and the list reloads with a globe on it.

A shared flow opened from a workspace shows 🏠, *Move to this workspace*, which
moves it out of `global` into that workspace: other workspaces stop seeing it.
The move writes the flow into the new library, then deletes it from the old.
A workspace whose config filters the shared library may not be offered the
flow it just moved: the list shows it dimmed, `not offered here`.

## Routes

The view is in the address's hash, so a link opens a tab directly:

| Hash | View |
|---|---|
| `#/` | the workspace list |
| `#/secrets`, `#/settings`, `#/console` | that top tab |
| `#/workspaces/<key>` | a workspace, on Session |
| `#/workspaces/<key>/<tab>` | `session`, `files`, `flows`, `site-data`, `history`, `console`, `network`, `live` or `config` |
| `#/workspaces/<key>/flows/<name>` | Flows with that flow open |
| `#/workspaces/<key>/site-data/<host>` | Site data with that host open |
| `#/workspaces/<key>/network/<id>` | Network with that request open |
| any workspace hash, then `?session=<name>` | that sub-session picked |

A person holding the server's token can do the same over HTTP, with
`Authorization: Bearer <token>`, under `ROUTE_PREFIX`:

| Route | Does |
|---|---|
| `GET /admin/workspaces` | the workspace list |
| `DELETE /admin/workspaces/{key}/session` | End browser; `?session=<name>` for a sub-session |
| `POST /admin/workspaces/{key}/end-all` | End all |
| `POST /admin/workspaces/{key}/flows/{name}/move` | Move, with `{"to": "global"}` or a workspace's name |
| `GET`, `PUT`, `DELETE /admin/workspaces/{key}/config` | read, write (`{"text": ...}`) or remove `workspace.yaml` |
| `DELETE /admin/workspaces/{key}/site-data`, `.../site-data/{site}` | Clear, or Forget one host |
| `DELETE /admin/workspaces/{key}/history` | Clear history |
| `DELETE /admin/workspaces/{key}/files/{name}` | delete one file; `screenshots`, `recordings` or `downloads` as the name clears that folder |

An agent never calls these: AGENT_AS_ADMIN.md.
