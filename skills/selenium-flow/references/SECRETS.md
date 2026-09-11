# Secrets — type a credential you are never shown

A secret is a named set of keys — `nextcloud` with `username` and `password`,
say — that an operator put on the server. You can use one. **You can never see
one.** No tool, resource or report returns a value, and you do not need it: you
name the secret where the value would go and the server types it.

That is the whole point. A password you hold is in your transcript, your tool
call, and whatever your host logs. A password you *name* is in none of them.

## The loop

1. **Find the secret.** `list_secrets` (or read `secret://secrets`). Read the
   names, the keys and the sites each may be used on.
2. **Find the selectors.** Drive the login page by hand once and `extract` to
   find the fields (`references/READING_PAGES.md`). Type nothing real yet.
3. **Build the flow.** The password step names the secret instead of a value.
4. **Save it.** `save_flow`, once (`references/FLOWS.md`).
5. **Run it.** `run_flow`, forever after, with no credential anywhere.

## What the listing tells you

```json
{"name": "nextcloud", "keys": ["password", "username"],
 "description": "Admin login for the homelab Nextcloud",
 "allowed_urls": ["https://nextcloud.example.com"], "restricted": true,
 "source": "filesystem"}
```

| Field | Means |
|---|---|
| `keys` | what you may name as `key` — never the values |
| `allowed_urls` | the only sites it may be typed on, as exact origins |
| `restricted: false` | no sites declared: it may be used anywhere |
| `allowed_urls_rejected` | its site list is broken, so it cannot be used at all until an operator fixes it |

If the tool says secrets are not enabled, the server was started without any —
there is nothing to find, and asking again will not change it.

## Binding one

Give `write` a `value_from` **instead of** `text`:

```
write(css="#password", value_from={"secret": {"name": "nextcloud", "key": "password"}})
```

It comes back with `"value_from": "secret"` and no value. In a flow, the same
`value_from` goes in the step's `params`:

```json
{
  "name": "nextcloud-login",
  "description": "Log in to Nextcloud as the admin",
  "steps": [
    {"tool": "navigate", "params": {"url": "https://nextcloud.example.com/login"}},
    {"tool": "write", "params": {"css": "#user",
      "value_from": {"secret": {"name": "nextcloud", "key": "username"}}}},
    {"tool": "write", "params": {"css": "#password",
      "value_from": {"secret": {"name": "nextcloud", "key": "password"}}}},
    {"tool": "interact", "params": {"action": "click", "css": "button[type=submit]"}},
    {"tool": "extract", "params": {"css": "h1"}, "return": true}
  ]
}
```

The report shows that step as `text=<hidden>`.

The rules, each of which is refused rather than guessed at:

- **Only `write` takes a secret.** It is the one action that types a value into
  a field and does nothing else with it.
- **Exactly one source**, and never `text` as well.
- **Navigate first, in its own step.** A `write` binding a secret may not also
  take a `url`. The site check reads the page the browser is on at the moment
  of typing; a `write` that navigated would be checked against the page it was
  leaving.

## Allowed sites

A restricted secret is typed only when the browser is on one of its
`allowed_urls`, compared as an exact origin — scheme, host and port. A page on
`https://nextcloud.example.com.evil.test` is not a match, and neither is plain
`http://`. A refusal names the page it was on and the sites it would accept, so
if you see one, check where the browser actually is before anything else.

## A parameter or a secret?

**A parameter is for what varies between runs. A secret is for what must not be
seen.** An email address is a parameter. Its password is a secret.

If a caller has to supply a sensitive value itself, declare the parameter
`"writeOnly": true`: it is typed as usual and hidden from the report. But the
caller still held it — only a secret keeps it out of everyone's hands.

## What this does not protect

Once typed, the value is **in the page**, and anything that reads the page can
read it back — `execute_script` most obviously. The server scrubs it from every
report, error and URL it returns to you; it cannot unsee the DOM. A flow that
binds a secret is not a sandbox. Do not read a field you have just filled with
one, and do not screenshot a page that shows it.

Filesystem secrets are visible to every session on the server. The listing is
not scoped to you, so treat every name you see as shared.
