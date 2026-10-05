# The Skills extension: `skills/list` and `skills/get` for the embedded skill

**Status: SPEC, 2026-10-05, branch `skills-extension`. Plan:
`docs/superpowers/plans/2026-10-05-skills-extension.md`.** Dr K reads this as it
grows and drops rulings in chat; every ruling is recorded under *Rulings*.

## Brief

Dr K, 2026-10-05, after reading how mcp-kb serves the Skills extension (#23, from
flujoapp): *"For this skills extension to be registered do we need a get_skills and
list_skills on selenium flow … or can it work through the get list resources? I want
to fully support the skills extension in selenium flow as well."* And: *"I want to
try with Claude web … maybe Claude web supports the skills extension. Either way
let's do it."*

## Research that shaped it

**The extension is protocol methods, not tools.** SEP-2640
(`io.modelcontextprotocol/skills`, Final since 2026-09-13, spec in
`modelcontextprotocol/ext-skills`, `specification/stable/skills.mdx`) adds two
JSON-RPC methods beside `resources/list`: `skills/list` and `skills/get`. A client
calls them only after it sees the extension declared in the server's capabilities;
to any other client `skill://` URIs are ordinary resources. Content still travels by
`resources/read`. So serving resources alone, as selenium-flow does today, is not
the extension, and no tool is involved.

**What the spec requires of a server:**

- Declaring the extension requires declaring the `resources` capability too.
- An entry is `{uri, frontmatter, resources}`. `uri` is the skill's `SKILL.md`;
  `frontmatter` is its YAML header as JSON, with `name` and `description` (1–1024
  characters) at minimum.
- `resources` is either `"dynamic"` or a **complete** array: every file of the
  skill, each exactly once, `SKILL.md` included, each `{uri, digest, size}` with
  `digest` = `sha256:<hex>`. A host verifies what it reads against these, and may
  read only these URIs for the skill.
- `skills/list` results extend `CacheableResult`: `ttlMs` and `cacheScope` are
  required. Pagination is the base protocol's: an optional cursor, `nextCursor` when
  there is more.

**How mcp-kb does it** (`kubed/mcp_kb/mcp/skills.py`, about 100 lines): a FastMCP
`ServerExtension` whose `methods()` binds the two methods with `MethodBinding`,
added by `mcp.add_extension(...)`; and an `on_initialize` middleware that puts the
declaration back into a legacy `initialize` reply. Digests are of the bytes
`resources/read` returns, not the bytes on disk. Results carry
`resultType: "complete"`, `ttlMs: 0`, `cacheScope: "private"`. An unknown skill is
`INVALID_PARAMS` "Skill not found"; a cursor is `INVALID_PARAMS` "Invalid skills
cursor".

**FastMCP 4.0.11, probed in the pod:** `add_extension` declares the extension in
`server/discover` (modern mode), but legacy `initialize` returns
`capabilities.extensions = None` while still answering `skills/list`. The legacy
middleware is needed. An unregistered method is `Method not found`. FastMCP has no
built-in SEP-2640 support yet; its issue #5016 proposes one for resource-backed
skills.

**FastMCP's `SkillProvider`** (what serves the skill today) reads a text file with
`read_text(encoding="utf-8")`, which normalises newlines, and any other file as
bytes. Its `_manifest` hashes the file on disk. Its `skill_info` exposes the
folder, the main file, and the relative path of every file it serves.

**Who can use it.** The official client matrix
(`modelcontextprotocol.io/extensions/client-matrix`) marks ChatGPT, fast-agent and
MCP Inspector *Partial*; **Claude (web), Claude Desktop and VS Code Copilot have no
entry**.

**The gateway blocks it.** Probed 2026-10-05 against mcp-kb with a real Keycloak
token: through `https://mcp.kellyferrone.com/kb/mcp` the declaration arrives in
`initialize`, but `skills/list` fails with HTTP 500, `unsupported method:
skills/list` (agentgateway's `InvalidMethod`). Directly in-cluster mcp-kb answers
it. agentgateway 1.6.0 is the latest release; upstream issue
[agentgateway#3579](https://github.com/agentgateway/agentgateway/issues/3579), "MCP
Skills Support", is open, and a maintainer agreed to a single-server passthrough.

## Rulings

1. **Server side only; wait for the gateway** (Dr K, 2026-10-05). Build the
   extension as designed, test it in-cluster and with MCP Inspector, and let
   agentgateway#3579 make it reachable through `mcp.`/`mcp-pub.` later. No
   plain-HTTP gateway route, no conditional declaration. mcp-kb is already in the
   same state.
2. **Secrets in the app, added to this PR** (Dr K, 2026-10-05, mid-build): *"give
   the mcp-apps a view for the secrets list or one secret. the value would never
   be passed over the call, this would be purely read only like the admin UI."*
   One secret opens **inside the app** from the list data `show` already sent; no
   `secret://secrets/{name}` resource (Dr K chose this over adding one), so
   `secrets.py`'s "one read" rule (§F1.31) stands.

## Goal

A Skills-aware client connected to selenium-flow discovers the embedded skill with
`skills/list`, fetches it with `skills/get`, and verifies every file it reads
against the entry's digests.

## Non-goals

- Anything in the cluster repo or the gateway (Ruling 1).
- Changing `resources/list`, `_manifest`, the `read_resource`/`list_resources`
  mirror tools, or the instructions. A client that ignores the extension sees no
  difference.
- `"dynamic"` resources, pagination, or more than one skill: there is one skill and
  it is static.
- Declaring the extension conditionally per client.

## The design

### The extension

A new module, `kubed/selenium_flow/mcp/skill_extension.py`, beside `skill.py`.
`skill.py`'s docstring says serving the skill is FastMCP's job; this part is ours
until FastMCP ships #5016, so it lives apart and is easy to delete then.

- `EXTENSION_ID = "io.modelcontextprotocol/skills"`.
- `entry_for(provider) -> dict | None` builds the one entry from the
  `SkillProvider`, or returns `None` (with a warning logged) if the skill does not
  conform.
- `SkillsExtension(ServerExtension)` holds that entry and binds
  `skills/list` (`PaginatedRequestParams`) and `skills/get` (`GetSkillParams`, a
  `RequestParams` with a required, non-empty `uri`).
- `AdvertiseSkills(Middleware)`: `on_initialize` adds `{EXTENSION_ID: {}}` to
  `result.capabilities.extensions`, keeping any already there.
- `register(mcp, provider) -> bool` builds the entry, and if it has one adds the
  extension and the middleware; returns whether it did.

`skill.register(mcp, provider)` calls `skill_extension.register` after
`mcp.add_provider(provider)`. With `--mcp-skill false` there is no provider, so
`skill.register` is never called and nothing is declared.

### The entry

Built once, when the server is constructed. Package data cannot change under a
running process, so a per-request rebuild buys nothing.

- `uri`: `skill://selenium-flow/SKILL.md`, formed the way `SkillProvider` forms it:
  `skill://<folder>/<main file>`.
- `frontmatter`: the YAML between the first two `---` lines of `SKILL.md`, parsed
  with `yaml.safe_load`, kept exactly as parsed. Not `skill_info.frontmatter`,
  which may come from FastMCP's line-based fallback.
- `resources`: one item per file in `skill_info.files` (which includes `SKILL.md`),
  sorted by URI, `{uri: skill://<folder>/<path>, digest: sha256:<hex>, size}`.
  Digest and size are of **the bytes `resources/read` returns**: for a file whose
  guessed MIME type is `text/*`, `read_text(encoding="utf-8").encode("utf-8")`;
  otherwise `read_bytes()`. That mirrors `SkillProvider`'s read exactly, and the
  tests prove it over the wire.

**Conformance check.** `entry_for` returns `None` and logs a warning when:
- the frontmatter is not a mapping, or does not survive
  `json.dumps(..., allow_nan=False)`;
- its `name` is not the folder name;
- its `description` is not a string of 1–1024 characters.

The resources keep being served. Bad package data must not stop the boot; that is
`skill.load()`'s rule already.

### The results

Both methods return `resultType: "complete"`, `ttlMs: 0`,
`cacheScope: "private"`, the same as mcp-kb, plus:

- `skills/list` → `skills: [entry]`. A cursor gets `INVALID_PARAMS` "Invalid skills
  cursor": there is only ever one page.
- `skills/get` → `skill: entry` when `uri` is the entry's URI; anything else gets
  `INVALID_PARAMS` "Skill not found".

The entry dict is shared, so each result carries a deep copy: a caller's mutation
cannot change what the next caller is served.

### Authentication

Nothing new. The methods arrive on `/mcp` like any request, behind the same token
or JWT check.

### Secrets in the app (Ruling 2)

- `show.py`: one more `VIEWS` row, `secret://secrets` → `secrets`; `NOUNS` gains
  `secrets: secret`, so the model's line reads "…: 1 secret." With no catalogue
  the resource's own refusal ("secrets are not enabled…") comes back as the tool
  error. `secret://secrets/{name}` stays unshowable.
- The app: `SecretsView` draws the catalogue as a horizontal card scroller, like
  the flows. Each card shows the name, description, key count, reach and the
  warning pills. A click opens that one secret in place as the admin pane's full
  card (keys with their sources, allowed sites, the reasons it may fail, origins),
  with "← All secrets" to go back. It needs no server call, so it works where the
  host offers no `callServerTool`.
- One card for both surfaces: the admin pane's card snippet becomes
  `lib/SecretCard.svelte`, and its helpers move to `lib/secrets.ts`. The admin and
  the app then read an entry the same way. The admin's DOM is unchanged, which its
  tests prove.
- Never a value: the catalogue has none. A test configures a secret with known
  values and checks that neither appears anywhere in `show`'s result.

## Testing

`tests/test_skills_extension.py`, unit-marked, through FastMCP's in-memory
`Client` (the `server` fixture, which is how the server is deployed), sending the
methods with `client.session.send_request` as mcp-kb's test does. Where it says
*both modes*, it is parametrised over `mode="auto"` and `mode="legacy"`.

1. **Declared, in both modes**: `server_capabilities.extensions[EXTENSION_ID] ==
   {}` and `server_capabilities.resources` is set.
2. **Listing**: `skills/list` returns exactly one entry, with URI
   `skill://selenium-flow/SKILL.md`, and the three result fields.
3. **`skills/get` agrees**: it returns the same entry as the listing.
4. **Every digest verifies, in both modes**: each listed file is read with
   `client.read_resource`, and its size and sha256 match the entry.
5. **The list is complete**: its URIs equal the `skill://selenium-flow/...`
   resources the server lists, minus `_manifest`. So a reference added later can
   never be left out.
6. **Frontmatter**: it equals the YAML header of the `SKILL.md` that
   `resources/read` returns.
7. **Refusals**: an unknown URI and a cursor are each `MCPError` with
   `INVALID_PARAMS`.
8. **Skill off** (`mcp={"skill": False}`): no declaration, and `skills/list` is
   `Method not found`.
9. **Non-conforming skill**: `entry_for` on a provider over a temporary skill whose
   `name` differs from its folder returns `None`, and `register` adds nothing.
10. **Isolation**: mutating a returned entry does not change the next result.

## Documentation

- `AGENTS.md`, *The embedded skill*: a paragraph on the extension. It covers:
  - the module;
  - the digests are of the served bytes, not the `_manifest`'s disk bytes;
  - both eras must declare it;
  - agentgateway 1.6 refuses the methods (#3579).
- `README.md`, *It teaches you how to use it*: one sentence. Skills-aware clients
  also find the skill through the Skills extension (`skills/list`, `skills/get`).
- `config.py`: the `mcp.skill` description becomes "Serve the agent skill: resources and
  the Skills extension."; `wiki/Configuration.md` is
  regenerated with `scripts/generate_wiki.py`.
- `CHANGELOG.md` `[Unreleased]`: one line, "Skills-aware clients discover the
  embedded skill through the MCP Skills extension (`skills/list`, `skills/get`)."

## Live check after deploy

Through the gateway the methods are refused (Research), so:

- In-cluster, from the pod: `initialize` (legacy) shows the declaration;
  `skills/list` returns the entry; every file read back matches its digest.
- Optionally, MCP Inspector through a port-forward, for Dr K to see it in a real
  client.
- Through `mcp.`/`mcp-pub.`: confirm the declaration arrives and the call is
  refused, which records where agentgateway stands.

## Next round

- When agentgateway ships #3579: re-run the gateway probe; nothing should change
  here.
- When FastMCP ships #5016: replace `skill_extension.py` with FastMCP's own.
