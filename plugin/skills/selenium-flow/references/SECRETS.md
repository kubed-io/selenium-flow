# Secrets

A secret is a named set of keys, such as `nextcloud` with `username` and
`password`, that an operator put on the server. You name the secret where the
value would go and the server types it. No tool, resource or report returns a
value, so it is never in your transcript.

## Finding one

Read `secret://secrets`:

```json
{"name": "nextcloud", "keys": ["password", "username"],
 "description": "Admin login for Nextcloud",
 "allowed_urls": ["https://nextcloud.example.com"], "restricted": true,
 "source": "filesystem"}
```

| Field | Means |
|---|---|
| `keys` | what you may pass as `key` |
| `allowed_urls` | the only origins it may be typed on |
| `refused` | why it cannot be used and how an operator fixes it; tell the person, do not retry |
| `restricted: false` | no sites declared, so it is `refused` |
| `allowed_urls_rejected` | its site list has a bad line, so it is `refused` |

If the listing says secrets are not enabled, the server has none. Filesystem
secrets are visible to every workspace on the server, so treat each name as
shared.

## Typing one

```
write(selector={"css": "#password"}, secret={"name": "nextcloud", "key": "password"})
```

The result has `"text_from": "secret"` and no value. In a flow the same `secret`
goes in the step's `args`:

```json
{
  "name": "nextcloud-login",
  "description": "Sign in to Nextcloud as the admin",
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

The `assert` proves the sign-in worked, and `save_site_data` keeps it
(SITE_DATA.md). The report shows the typing steps as
`text=[secret:nextcloud/password]`. Any later read shows that marker,
`[secret:name/key]`, where a secret's value was, and `write` with that name and
key types it again. A key set `masked: false` is never shown as a marker.

Each of these is refused:

- a `secret` on any tool but `write`;
- a `secret` with anything but `name` and `key`, or beside `text`;
- a `${parameter}` in place of the name or key in a flow;
- a `url` on a `write` that binds a secret: navigate first, in its own call or
  step, because the site check reads the page the browser is on when it types.

## Where it may be typed

A secret is typed only when the browser is on one of its `allowed_urls`, compared
as an exact origin: scheme, host and port. `https://nextcloud.example.com.evil.test`
does not match, and neither does `http://`. A refusal names the page it was on
and the origins it accepts, so check where the browser is first.

Inside a frame, both the page's and the frame's origin must be allowed. A `write`
into an element that hosts another document (`iframe`, `frame`, `object`,
`embed`) is refused.

For what varies between runs, such as an email address, use a flow parameter;
for what nobody may see, such as its password, use a secret. `save_flow` refuses
a parameter marked `"writeOnly": true`.

## What the scrub does not cover

Once typed, the value is in the page. The server scrubs it from every report,
error, URL and resource it returns, but not from the DOM. Do not read back a
field you have just filled from a secret, and do not screenshot a page that
shows it. The scrub cannot reach:

- pixels: a screenshot, the live view and a recording show the value in any
  field that is not a password field;
- `execute_script`, which can transform a value past every encoding the scrub
  knows and send it away with its own `fetch`;
- non-ASCII case variants of a value (percent, JSON, HTML, base64, hex and
  reversed forms are matched);
- a file opened from its signed link, a text file included. Read a text file
  through its `workspace://` resource instead, which is scrubbed;
- what the site stores: a token kept in localStorage or sessionStorage is saved
  by `save_site_data` and shown in `workspace://site-data/{site}`. httpOnly
  cookies stay masked.
