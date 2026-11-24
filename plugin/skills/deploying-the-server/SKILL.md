---
name: deploying-the-server
description: Use when deploying or configuring the selenium-flow server, connecting an MCP or HTTP client to it, setting a client's session defaults, or putting secrets or OIDC on the server.
---

# Deploying the server

The operator's side of selenium-flow: what the server needs, the settings, how
a client connects, and what only the server's own configuration can change.

| When | Read, in `skill://selenium-flow/deploying-the-server/references/CONFIGURATION.md` |
|---|---|
| What it needs, a Grid, health checks | *What it needs* |
| A setting, its env var, where a value came from | *Settings and where they come from* |
| Connecting an MCP or HTTP client, a workspace header | *Connecting a client* |
| A client's default browser, window or timeouts | *A client's session defaults* |
| Uploads failing with no writable directory | *Uploads need a writable directory* |
| Adding a secret, where it may be typed, a secret refused | *Secrets on the server* |
| Letting an agent sign in to the admin page | *The admin token as a secret* |
| An OIDC issuer for `/mcp` or the admin page, its client and redirect URI | *OIDC* |

Every setting, in all three spellings, is on the wiki's
[Configuration](https://github.com/kubed-io/selenium-flow/wiki/Configuration)
page. Crossing over: what a person does on the running server's admin page is
`skill://selenium-flow/using-the-admin-ui/SKILL.md`.
