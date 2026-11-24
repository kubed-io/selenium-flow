# Doing admin work yourself

You can do what a person does on the admin page, through your own browser and
nothing else. No tool calls the admin routes, and nothing returns a secret's
value. Signing in to the page changes nothing about your MCP calls: they act as
they did before.

## The rules

1. Ask the person first, and show them the change, or the YAML, before you make
   it.
2. Never `save_site_data` on the admin page. It saves the tab's
   `sessionStorage`, where the page keeps the token.
3. Never `execute_script` there, to reach the token or to call the admin API.
   Click what a person would click.
4. Never take control in Live. Only a person holds a browser.
5. Take a destructive action (End, End all, Delete, Clear, Forget, Move) only
   when the person has named its target, and click its confirm yourself.
6. Stay out of other workspaces' Site data and Network detail. The admin page is
   not scrubbed: it shows what an agent's own reads would mask.

## What cannot be done, by anyone, from the page

- Creating, changing or rotating a secret. An operator does that on the server.
- Changing a server setting: Settings is read-only.
  `skill://selenium-flow/deploying-the-server/SKILL.md` covers both.
- Anything at all on a server reached over stdio, which has no admin page.

## Signing in with the token

The operator stores the server's token as a secret, for example `admin` with
the key `token`, allowed on the exact origin your Grid browser loads the admin
page from (`skill://selenium-flow/deploying-the-server/references/CONFIGURATION.md`,
*The admin token as a secret*). Check `secret://secrets` for it first: without
it, tell the person, and guide them instead (ADMIN_UI.md).

```
open_session(session="admin")
navigate(url="https://flows.example.com/", session="admin")
write(selector={"css": "#token"}, secret={"name": "admin", "key": "token"}, session="admin")
interact(action="click", selector={"xpath": "//button[@type='submit']"}, session="admin")
navigate(url="https://flows.example.com/#/workspaces/research-bot/flows/sign-up", session="admin")
```

- A sub-session of its own keeps your task's browser as it was.
- `navigate` comes first, in its own call: a `write` that types a secret takes
  no `url`.
- Go to a view by its hash route (ADMIN_UI.md, *Routes*). `config_url` on
  `workspace://current` is the Config tab's.
- A box's confirm is a button naming the verb, such as **Move**. End browser
  asks with the browser's own dialog: answer it with `dialog(action="accept")`.
- `end_browser(session="admin")` when the work is done. The token goes with the
  browser, since nothing saved it.

## Signing in with OIDC

When the admin page offers **Sign in with OIDC**, the operator can instead store
an account at the issuer that holds an admin role, as a secret allowed on the
issuer's origin. Click the button (`#oidcSignIn`), then `write` the username and
password into the issuer's own form, which is on the issuer's origin, and submit
it. The browser comes back to the admin page signed in. The audit then names that
account rather than `admin`. The operator registers the admin page's address as
the client's redirect URI first.
