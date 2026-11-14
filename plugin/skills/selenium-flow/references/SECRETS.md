# Secrets — type a credential you are never shown

A secret is a named set of keys — `nextcloud` with `username` and `password`,
say — that an operator put on the server. You can use one. **You can never see
one.** No tool, resource or report returns a value, and you do not need it: you
name the secret where the value would go and the server types it.

That is the whole point. A password you hold is in your transcript, your tool
call, and whatever your host logs. A password you *name* is in none of them.

## The loop

1. **Find the secret.** Read `secret://secrets`: the names, the keys and the
   sites each may be used on. `show(secret://secrets)` draws them for the
   person.
2. **Find the selectors.** `outline` the login page: it gives each field a
   checked selector (`skill://selenium-flow/references/READING_PAGES.md`). Type
   nothing real yet.
3. **Build the flow.** The password step names the secret instead of a value.
4. **Save it.** `save_flow`, once (`skill://selenium-flow/references/FLOWS.md`).
5. **Run it.** `run_flow`, forever after, with no credential anywhere.

## What the listing tells you

```json
{"name": "nextcloud", "keys": ["password", "username"],
 "description": "Admin login for the homelab Nextcloud",
 "allowed_urls": ["https://nextcloud.example.com"], "restricted": true,
 "source": "filesystem"}
```

An entry no site is declared for is listed with `refused` instead, and cannot be used:

```json
{"name": "legacy", "keys": ["token"], "restricted": false,
 "refused": "no allowed sites are declared, so it cannot be used: add an `_allowed_urls` file ...",
 "source": "filesystem"}
```

| Field | Means |
|---|---|
| `keys` | what you may name as `key` — never the values |
| `allowed_urls` | the only sites it may be typed on, as exact origins |
| `restricted` false | no sites declared, so `refused` is set and it cannot be used |
| `refused` | a sentence saying why it cannot be used and how an operator fixes it: tell the person, do not retry |
| `allowed_urls_rejected` | its site list is broken, so it is `refused` until an operator fixes it |

If the tool says secrets are not enabled, the server was started without any —
there is nothing to find, and asking again will not change it.

## Binding one

Give `write` a `secret` **instead of** `text`:

```
write(selector={"css": "#password"}, secret={"name": "nextcloud", "key": "password"})
```

It comes back with `"text_from": "secret"` and no value. In a flow, the same
`secret` goes in the step's `args`:

```json
{
  "name": "nextcloud-login",
  "description": "Log in to Nextcloud as the admin",
  "steps": [
    {"tool": "navigate", "args": {"url": "https://nextcloud.example.com/login"}},
    {"tool": "write", "args": {"selector": {"css": "#user"},
      "secret": {"name": "nextcloud", "key": "username"}}},
    {"tool": "write", "args": {"selector": {"css": "#password"},
      "secret": {"name": "nextcloud", "key": "password"}}},
    {"tool": "interact", "args": {"action": "click", "selector": {"css": "button[type=submit]"}}},
    {"tool": "assert", "args": {"script": "return !location.pathname.startsWith('/login')",
      "stable_for": 1, "message": "Still on the login page."}},
    {"tool": "save_site_data", "args": {}}
  ]
}
```

The report shows the typing steps as `text=[secret:nextcloud/password]`. That marker, `[secret:name/key]`, is also what any later read on an allowed site shows where a secret's value was: it means a secret was here, and `write` with that name and key types it again. The `assert` is what proves
you are in — `extract` reads content and never stops a flow. The last step keeps
the sign-in:
a reopened browser comes back signed in, and the password is not typed again
(`skill://selenium-flow/references/SITE_DATA.md`).

The rules, each of which is refused rather than guessed at:

- **Only `write` takes a secret.** It is the one action that types a value into
  a field and does nothing else with it.
- **Exactly one source**, and never `text` as well.
- **Navigate first, in its own step.** A `write` binding a secret may not also
  take a `url`. The site check reads the page the browser is on at the moment
  of typing; a `write` that navigated would be checked against the page it was
  leaving.

## Allowed sites

Every secret names the sites it may be used on, and only there; one with no sites declared is refused, not allowed everywhere. A secret is typed only when the browser is on one of its
`allowed_urls`, compared as an exact origin — scheme, host and port. A page on
`https://nextcloud.example.com.evil.test` is not a match, and neither is plain
`http://`. A refusal names the page it was on and the sites it would accept, so
if you see one, check where the browser actually is before anything else.

**Inside a frame, both must be allowed.** After `frame`, the check reads the
page and the frame's origin, and refuses naming the first that is not allowed:
an allowed site's page framing another site does not let the secret into that
frame, and an allowed frame inside another site's page does not either.

## A parameter or a secret?

**A parameter is for what varies between runs. A secret is for what must not be
seen.** An email address is a parameter. Its password is a secret.

There is no middle option: `save_flow` refuses a parameter marked
`"writeOnly": true`, because it would hide nothing. If a caller has to supply a
sensitive value itself, it already holds the value; only a secret keeps it out
of everyone's hands.

## What this does not protect

Once typed, the value is **in the page**, and anything that reads the page can
read it back — `execute_script` most obviously. The server scrubs it from every
report, error and URL it returns to you; it cannot unsee the DOM. A flow that
binds a secret is not a sandbox. Do not read a field you have just filled with
one, and do not screenshot a page that shows it.

What the scrub cannot reach:

- **Pixels.** A screenshot, the live view and a recording show a value in any
  field that is not a password field, and a screenshot's image is never scrubbed.
- **`execute_script`** can transform a value in ways no encoding list
  anticipates, and can send it away with its own `fetch`.
- **Non-ASCII case variants** of a value are not matched; the rest of the
  spellings (base64, hex, reversed, any ASCII case) are.
- **A file opened from its signed link** (a kept file, a download, a
  screenshot, a recording) is served as it is, unscrubbed, a text file
  included. Read a text file through its `workspace://` resource instead,
  which is scrubbed.

A site that stores what you typed makes it readable in site data: a token typed
through a secret that the page keeps in localStorage or sessionStorage is saved
by `save_site_data` and shown in `workspace://site-data/{site}` and the admin UI.
httpOnly cookies stay masked
(`skill://selenium-flow/references/SITE_DATA.md`).

Filesystem secrets are visible to every workspace on the server. The listing is
not scoped to you, so treat every name you see as shared.
