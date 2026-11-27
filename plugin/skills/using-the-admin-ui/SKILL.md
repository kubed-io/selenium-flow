---
name: using-the-admin-ui
description: Use when a person asks about selenium-flow's admin page or wants admin work done - a workspace's config, a flow moved to the shared library, site data, history or files cleared, a browser ended, watched or taken over, or signing in to the admin page.
---

# Using the admin page

The admin page is selenium-flow's web page for people: a person signs in there
to watch and tidy what agents do. An agent needs it for its own task only when
its workspace's config is refused.

| When | Read |
|---|---|
| What the page has, what each tab and button does, a flow moved into or out of the shared library, a route to open | `references/ADMIN_UI.md` |
| Signing in with the token or OIDC, a sign-in refused | `references/ADMIN_SIGN_IN.md` |
| A workspace's config (`workspace.yaml`, `flows.global`, `config_url`), an invalid config | `references/WORKSPACE_CONFIG.md` |
| A person watching or taking over a browser in Live | `references/LIVE_VIEW.md` |
| Being asked to do the admin work yourself | `references/AGENT_AS_ADMIN.md`, before anything else |

Read `references/AGENT_AS_ADMIN.md` before you open the admin page in a browser: its rules
are not optional.

Crossing over: a secret or a server setting changes on the server, never on the
page, the `deploying-the-server` skill. Your own refused call while a person
holds the browser is the `driving-a-browser` skill, row "A call refused because
a person holds the browser". The tools and the rules for driving the browser are
the `driving-a-browser` skill.
