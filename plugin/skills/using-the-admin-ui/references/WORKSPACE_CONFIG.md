# A workspace's config

A workspace's config is `workspace.yaml` in its folder on the server, the
boundary a person set for the agents that use it. An agent reads it in
`workspace://current/config` and never writes it.

## Managing a workspace

A workspace can have a config, `workspace.yaml` in its folder. A workspace with
one is kept: it never expires. `workspace://current/config` shows each setting,
its value and whether the file set it. Today it holds one setting:

| Setting | Means |
|---|---|
| `flows.global` | which shared flows this workspace is offered: rows of tags joined by commas; a flow carrying every tag of any row passes. Unset offers them all |

Only an admin changes the config. You never can: no tool writes it, and that is
the point, since it is the boundary a person set for you. When a person wants
it changed, write the new YAML for them and guide them, in this order:

1. **The admin UI.** Give them `config_url` from `workspace://current`, which
   opens this workspace's Config tab, and the YAML to paste into its editor:

   ```yaml
   flows:
     global:
     - shop,login
     - reports
   ```

2. **By hand, when there is no admin UI** (no `config_url`, as on stdio). The
   file is `<data dir>/workspaces/<workspace>/workspace.yaml` on the server, and
   the next call reads it. Give them the whole file:

   ```yaml
   flows:
     global:
     - shop,login
   ```

3. **With selenium-flow itself.** If the server's admin token is held as a
   secret (an operator names it, for example `admin` with the key `token`), you
   can drive the admin UI in your own browser: open the admin page, `write` the
   secret into the token field (`#token`), open `config_url`, and paste the
   YAML into the editor (`references/AGENT_AS_ADMIN.md` has the steps). Ask the person before
   you do, and show them this YAML first:

   ```yaml
   flows:
     global:
     - shop,login
   ```

A config that does not parse or validate refuses every call that names the
workspace, `workspace://current` included, with one sentence (over HTTP, a 409):

> this workspace's config is invalid: `colour` is not a setting workspace.yaml holds: it holds flows.global. An admin fixes it in the admin UI: https://flows.example.com/#/workspaces/research-bot/config

Nothing works until it is fixed. Tell the person the reason and the link, and
offer the corrected YAML.

## The Config tab

The admin page's Config tab, the last of a workspace's tabs, is where a person
changes it. `config_url` on `workspace://current` opens it directly, once the
server knows its public address.

- Each setting is a row with its value and a source pill: `workspace` when the
  file sets it, `default` when it does not. A `flows.global` row shows its rows
  of tags as chips; unset reads `every global flow`, and an empty list
  `no global flow`.
- **Edit YAML** opens the file in an editor. A save the server refuses keeps
  the editor open with the reason under it, and the file stays as it was.
- **Remove config**, on a kept workspace, deletes the file after a confirm. The
  workspace then expires again, `workspace.ttl` after its last use.
- A saved change applies from the next call that reads it, with nothing
  restarted. A call already running finishes under the config it started with.
- A refused config shows its reason in a banner on every tab of the workspace,
  with a way into the editor.
- The shared library, `global`, has no config.
