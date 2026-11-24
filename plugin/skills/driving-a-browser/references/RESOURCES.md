# Everything to read

Read a URI the way your client reads MCP resources: in Claude Code,
`ReadMcpResourceTool` with server `selenium-flow`. A Claude Code subagent reads
resources only when its agent definition's `tools:` lists `ReadMcpResourceTool`
and `ToolSearch`. A client that cannot read resources gets the tools
`read_resource(uri)` and `list_resources()`.

| URI | Is |
|---|---|
| `workspace://current` | your own session: page, browser, window, `live`, `in_frame`, `tabs`, and `sessions` |
| `workspace://current/{part}` | one key of that, for instance `url` or `grid_timeout`; null when there is none |
| `workspace://files` | Files, each file with a link, and the two folders below |
| `workspace://files/{name}` | one file in Files |
| `workspace://files/screenshots` | screenshots not kept yet |
| `workspace://files/screenshots/{name}` | one of those |
| `workspace://files/recordings` | recordings not kept yet, each with a link that plays it |
| `workspace://files/recordings/{name}` | one of those |
| `session://downloads` | your own session's downloads |
| `session://downloads/{name}` | one of those |
| `workspace://site-data` | your own session's sites with saved site data, never a value |
| `workspace://site-data/{site}` | one site of your own session's saved data; httpOnly values masked |
| `workspace://console` | what your own session's page logged; needs `open_session(capture=true)` |
| `workspace://console{?level,pages}` | the same, narrowed: `?level=error`, `?pages=2` |
| `workspace://network` | the requests your own session's page made, each with an id; needs capture too |
| `workspace://network{?filter,pages}` | the same, narrowed: `?filter=failed`, `?filter=xhr` |
| `workspace://network/{id}` | one of your own session's requests in full |
| `session://{name}` | a session by name, as `workspace://current` is your own; `default` is the workspace's own, and a name with no session is not found |
| `session://{name}/console` | that session's console, as `workspace://console` is yours |
| `session://{name}/console{?level,pages}` | the same, narrowed |
| `session://{name}/network` | that session's requests; `network_body(id, session=name)` for a body |
| `session://{name}/network{?filter,pages}` | the same, narrowed |
| `session://{name}/network/{id}` | one of that session's requests in full |
| `session://{name}/site-data` | that session's saved site data, never a value |
| `session://{name}/site-data/{site}` | one site of it; httpOnly values masked |
| `session://{name}/downloads` | that session's downloads, to `keep_file` or `upload_file` |
| `session://{name}/downloads/{file}` | one of those |
| `secret://secrets` | the secrets you may type, never their values |
| `flow://flows` | the flows you can run |
| `flow://flows/{name}` | one flow's parameters and steps |
| `flow://schema` | what a flow and its steps may contain |
| `skill://selenium-flow/<skill>/SKILL.md` | a skill: `driving-a-browser`, `building-flows`, `signing-in`, `using-the-admin-ui` and `deploying-the-server`; each one's references are beside it |
