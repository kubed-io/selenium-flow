# Site data — stay signed in across a reaped browser

A new browser starts empty. `save_site_data` keeps this one's cookies and the
storage of the sites this session has been to (see Limits), and every browser
opened for the session afterwards has them back — the one that replaces a reaped
browser, or the one after `end_browser()`.

## When to save

After a sign-in you have **confirmed**, never before: check the page shows you are
in, then save once.

```
write(url="https://app.example.com/login", selector={"css": "#user"}, text="a@example.com")
write(selector={"css": "#pw"}, secret={"name": "app", "key": "password"})
interact(action="click", selector={"css": "button[type=submit]"})
assert(script="return !!document.querySelector('#avatar')")   # am I in?
save_site_data()
```

One save covers every site a call ended on — the identity provider you signed in
at, and an app you signed in to earlier in the session. Save again after a
setting you want kept.

Each save **replaces** the last. A save after signing out saves you signed out.

The result names what was kept, never a value:

```json
{"saved": {"cookies": 14,
           "sites": ["https://app.example.com", "https://sso.example.com"],
           "skipped": []},
 "uri": "workspace://site-data"}
```

`skipped` names a site whose storage is not in this save, and why. One that could
not be read keeps what the last save had for it: "a service worker answered: save
while on this site" means the site's own worker answered first, so `navigate`
there and save again, and it is read from the page itself. One "left out: the
snapshot would pass 1000000 bytes" was read and then dropped, the sites visited
longest ago first: it is gone, not kept.

## What comes back

Everything is in place before `open_session` returns: the cookies, every site's
localStorage, and the sessionStorage of the page you saved on. `open_session`
says which sites, and never a value:

```json
{"site_data": {"restored": ["app.example.com", "sso.example.com"], "skipped": [],
               "uri": "workspace://site-data"}}
```

No `site_data` key means nothing happened: nothing was saved, or only cookies that
have since expired. When the Grid reaps your browser, the
next call reopens it and restores the same way; the first result that succeeds
after the reopen carries this report, once. A call that reopens and then fails
passes it on.

`skipped` names a cookie or a site that did not come back, and why. "the browser
did not keep it" means the browser took a cookie without an error and dropped it;
its host is then not in `restored`.

## Restored but still signed out

The site ended its own session — it expired or was revoked. Sign in again, then
`save_site_data()` again; nothing else is wrong.

## Start as a new user

```
open_session(restore_site_data=false)
```

Opens with nothing **and deletes** what was saved, so the next open is empty too.
Where the session has been is kept.

## Debugging with execute_script

Set or clear a flag to see what a reopened browser keeps:

```
execute_script(script="localStorage.setItem('seen', '1')")
save_site_data()
end_browser()
open_session()
execute_script(script="return localStorage.getItem('seen')")   # "1"
```

`document.cookie` shows what the page can see. `workspace://site-data/app.example.com`
shows what was saved, including httpOnly cookies the page cannot — their values
read `•••`, here and in the admin UI.

## Limits

- A save reads the sites a call **ended on**, for as long as the session keeps its
  history: a day by default, 100 sites at most, the page you are on always. A site
  passed through inside one call is not read, and an app last visited more than a
  day ago loses its saved storage at the next save — its cookies stay.
- IndexedDB is not saved (Firebase Auth keeps its sign-in there).
- sessionStorage belongs to one tab, so only the page you save on keeps it.
- A browser opened with `insecure=true` gets none of it back: it accepts any
  certificate, so saved cookies could reach whoever sits in the middle. What was
  saved is kept for the next secure browser.
- A script cannot set an httpOnly cookie; it comes back through the browser's own
  channel.
- Stored on the session and expires with it. Never on this server's disk; with the
  Redis store it is as durable as Redis.
- A site that stores what you typed makes it readable in site data — a token typed
  through a secret and kept in localStorage shows in `workspace://site-data/{site}`.
  httpOnly cookies stay masked.
