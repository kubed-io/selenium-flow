---
name: signing-in
description: Use when a page in a selenium-flow browser asks for a password, token or other credential, a secret is refused, the browser should stay signed in across browsers, or a site signed it out unexpectedly.
---

# Signing in

Credentials in selenium-flow are secrets an operator put on the server, typed
by name so their values never reach you; a sign-in lasts past the browser only
as saved site data.

| When | Read |
|---|---|
| A password, token or key to type; finding a secret; a secret refused, or not allowed on this page | `skill://selenium-flow/signing-in/references/SECRETS.md` |
| Staying signed in across browsers, a sign-in to keep, starting as a new user | `skill://selenium-flow/signing-in/references/SITE_DATA.md` |
| Signed in before and signed out now, or saved and still signed out | `skill://selenium-flow/signing-in/references/SITE_DATA.md`, *Signed in before, signed out now* |

Crossing over: a sign-in worth repeating is a flow,
`skill://selenium-flow/building-flows/references/FLOWS.md`. A person can sign in
for you by taking over the browser,
`skill://selenium-flow/driving-a-browser/references/CONTROL.md`. Adding a secret
or changing where it may be typed is the operator's,
`skill://selenium-flow/deploying-the-server/references/CONFIGURATION.md`.
