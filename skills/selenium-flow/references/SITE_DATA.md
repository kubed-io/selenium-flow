# Site data — stay signed in across a reaped browser

A new browser starts empty. `save_site_data` keeps this one's cookies and storage
on your session, and every browser opened for the session afterwards gets them
back — the one that replaces a reaped browser, or the one after `end_browser()`.

## When to save

After a sign-in you have **confirmed**, never before: check the page shows you are
in, then save.

```
write(url="https://app.example.com/login", selector={"css": "#user"}, text="a@example.com")
write(selector={"css": "#pw"}, secret={"name": "app", "key": "password"})
interact(action="click", selector={"css": "button[type=submit]"})
assert(script="return !!document.querySelector('#avatar')")   # am I in?
save_site_data()
```

Save again after a setting you want kept. A save replaces the cookies and adds this
page's storage beside other sites', so signing in to a second site keeps the first.

## What comes back

Cookies are in place before the first page. Storage for the site you open on is
there when it loads; storage for other sites arrives when you first navigate to
them. `open_session` says which, and never a value:

```json
{"site_data": {"restored": ["app.example.com"],
               "waiting": ["https://keycloak.example.com"],
               "uri": "session://site-data"}}
```

`waiting` is normal. A later call that lands on it reports `restored` once. No
`site_data` key means nothing happened.

Each site's storage is filled once, when the browser first arrives there. A new
tab on a site that already arrived is not refilled, so what the app changed since
stands.

`skipped` names a cookie that did not come back and why. "the browser did not
keep it" means the browser accepted it without an error and dropped it; its host
is then not in `restored`.

## Restored but still signed out

The site ended its own session — it expired or was revoked. Sign in again, then
`save_site_data()` again; nothing else is wrong.

## Start as a new user

```
open_session(restore_site_data=false)
```

Opens with nothing **and deletes** what was saved, so the next open is empty too.

## Debugging with execute_script

Set or clear a flag to see what a reopened browser keeps:

```
execute_script(script="localStorage.setItem('seen', '1')")
save_site_data()
end_browser()
open_session()
execute_script(script="return localStorage.getItem('seen')")   # "1"
```

`document.cookie` shows what the page can see. `session://site-data/app.example.com`
shows what was saved, including httpOnly cookies the page cannot — their values
read `•••`, here and in the admin UI.

## Limits

- IndexedDB is not saved.
- A browser opened with `insecure=true` gets none of it back: it accepts any
  certificate, so saved cookies could reach whoever sits in the middle. What was
  saved is kept for the next secure browser.
- An origin reached only by a redirect or inside a frame is filled but not
  announced, and stays pending until you land on it yourself.
- A script cannot set an httpOnly cookie; it is restored through the browser's own
  channel.
- A save replaces all cookies, so a save after signing out saves you signed out.
- Stored on the session and expires with it. Never on this server's disk; with the
  Redis store it is as durable as Redis.
- A site that stores what you typed makes it readable in site data — a token typed
  through a secret and kept in localStorage shows in `session://site-data/{site}`.
  httpOnly cookies stay masked.
