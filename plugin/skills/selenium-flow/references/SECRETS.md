# Secrets

A secret is a named set of keys, such as `nextcloud` with `username` and
`password`, that an operator put on the server. You can use one but never see
one: no tool, resource or report returns a value. You name the secret where the
value would go and the server types it, so the value is never in your
transcript or your tool calls.

## The loop

1. **Find the secret.** Read `secret://secrets`: the names, the keys and the
   sites each may be used on. `show(secret://secrets)` draws them for the
   person.
2. **Find the selectors.** `outline` the login page
   (`skill://selenium-flow/references/READING_PAGES.md`).
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

An entry with no allowed sites is listed with `refused` and cannot be used:

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
| `refused` | why it cannot be used and how an operator fixes it: tell the person, do not retry |
| `allowed_urls_rejected` | its site list has a bad line, so it is `refused` until an operator fixes it |

If the listing says secrets are not enabled, the server has none, and asking
again will not change it.

## Binding one

Give `write` a `secret` instead of `text`:

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

The report shows the typing steps as `text=[secret:nextcloud/password]`. That
marker, `[secret:name/key]`, is also what any later read shows where a secret's
value was: a secret was here, and `write` with that name and key types it again.
The `assert` proves you are in, since `extract` never stops a flow. The last step
keeps the sign-in, so a reopened browser comes back signed in
(`skill://selenium-flow/references/SITE_DATA.md`).

The rules, each of which is refused rather than guessed at:

- Only `write` takes a secret.
- `secret` is exactly `name` and `key`, and `text` is not given as well. In a
  flow they are written literally, never as a `${parameter}`.
- Navigate first, in its own step. A `write` binding a secret may not also take
  a `url`, because the site check reads the page the browser is on when it
  types.

## Allowed sites

Every secret names the sites it may be used on, and a secret with none is
refused. A secret is typed only when the browser is on one of its `allowed_urls`,
compared as an exact origin: scheme, host and port. A page on
`https://nextcloud.example.com.evil.test` is not a match, and neither is plain
`http://`. A refusal names the page it was on and the sites it would accept, so
check where the browser is first.

Inside a frame, both the page and the frame's origin must be allowed, and the
refusal names the first that is not. A `write` into an element that hosts another
document (`iframe`, `frame`, `object`, `embed`) is refused.

## A parameter or a secret?

A parameter is for what varies between runs; a secret is for what must not be
seen. An email address is a parameter and its password is a secret.
`save_flow` refuses a parameter marked `"writeOnly": true`.

## What this does not protect

Once typed, the value is in the page, and anything that reads the page can read
it back, `execute_script` most obviously. The server scrubs it from every
report, error and URL it returns to you, but not from the DOM. Do not read a
field you have just filled with one, and do not screenshot a page that shows it.

What the scrub cannot reach:

- **Pixels.** A screenshot, the live view and a recording show a value in any
  field that is not a password field, and a screenshot's image is never scrubbed.
- **`execute_script`** can transform a value in ways no encoding list
  anticipates, and can send it away with its own `fetch`.
- **Non-ASCII case variants** of a value are not matched; the common encodings
  of it (percent, JSON, HTML, base64, hex, reversed) are.
- **A file opened from its signed link** (a kept file, a download, a
  screenshot, a recording) is served as it is, unscrubbed, a text file
  included. Read a text file through its `workspace://` resource instead,
  which is scrubbed.

A site that stores what you typed makes it readable in site data: a token typed
through a secret that the page keeps in localStorage or sessionStorage is saved
by `save_site_data` and shown in `workspace://site-data/{site}`; httpOnly cookies
stay masked (`skill://selenium-flow/references/SITE_DATA.md`).

Filesystem secrets are visible to every workspace on the server. The listing is
not scoped to you, so treat every name you see as shared.
