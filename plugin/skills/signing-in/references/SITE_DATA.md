# Site data

A new browser starts empty. `save_site_data` keeps the cookies and storage of
the sites your session has been to, and every browser your session opens
afterwards has them back before `open_session` returns: the one after
`end_browser()` and the one that replaces a reaped browser too. While your
session's browser is live, `open_session` joins it and restores nothing:
`fresh=true` puts saved site data back.

## When to save

After a sign-in you have confirmed, never before: check the page shows you are
in, then save once.

```
interact(action="click", selector={"css": "button[type=submit]"})
assert(script="return !!document.querySelector('#avatar')", message="Not signed in.")
save_site_data()
```

One save covers every site a call ended on, such as the identity provider you
signed in at and an app you signed in to earlier. Each save replaces the last,
so a save after signing out keeps you signed out.

```json
{"saved": {"cookies": 14,
           "sites": ["https://app.example.com", "https://sso.example.com"],
           "skipped": []},
 "uri": "workspace://site-data"}
```

Site data is each session's own. In a named session the `uri` is
`session://<name>/site-data`, and a new session starts with none.

`skipped` names a site whose storage is not in this save, and why:

- "a service worker answered: save while on this site": `navigate` there and
  save again. Until then the site keeps what the last save had.
- "left out: the snapshot would pass 1000000 bytes": the sites visited longest
  ago were dropped first, and their storage is gone.

## What comes back

The cookies, every site's localStorage, and the sessionStorage of the page you
saved on. `open_session` names the sites, never a value:

```json
{"site_data": {"restored": ["app.example.com", "sso.example.com"], "skipped": [],
               "uri": "workspace://site-data"}}
```

- No `site_data` key: nothing was saved, or only cookies that have since expired.
- After a reap, the first result carries this report once, beside `reopened`.
  If that call fails, its error says how many sites' data was put back.
- `skipped` with "the browser did not keep it": the browser took a cookie and
  dropped it, and its host is not in `restored`.
- Restored and still signed out: the site ended its own session. Sign in and
  save again.

`document.cookie` shows what the page can see. `workspace://site-data/{site}`
(`session://<name>/site-data/{site}` in a named session) shows what was saved,
httpOnly cookies included, with their values read as `•••`.

## Signed in before, signed out now

A new browser starts empty unless you saved site data. Sign in, confirm it, then
`save_site_data()`. Saved and still signed out means the site ended its own
session (*What comes back*, above).

## Starting as a new user

```
open_session(restore_site_data=false)
```

It opens with nothing and deletes what was saved, so the next open is empty too,
and says `site_data: {"forgotten": n}` when something was there. The workspace's
page history is kept.

On a live session, pass `fresh=true` with it:
`open_session(fresh=true, restore_site_data=false)`; without `fresh` the call
joins and deletes nothing.

## Limits

- A save reads the sites a call ended on, while the workspace keeps its history:
  a day by default, 100 sites at most, and always the current page. A site passed
  through inside one call is not read. An app last visited more than a day ago
  loses its saved storage at the next save, and keeps its cookies.
- IndexedDB is not saved, so a Firebase Auth sign-in does not come back.
- sessionStorage belongs to one tab: only the page you save on keeps it.
- A browser opened with `insecure=true` gets nothing back, since saved cookies
  could reach whoever sits in the middle. The saved data waits for the next
  secure browser.
- Site data is stored on the workspace and expires with it.
- A token typed from a secret that the page keeps in localStorage or
  sessionStorage is saved, and shows in `workspace://site-data/{site}`
  (`session://<name>/site-data/{site}` in a named session; `references/SECRETS.md`).
