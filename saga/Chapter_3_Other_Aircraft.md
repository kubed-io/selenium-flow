# Chapter 3 — Other Aircraft

> Flight log, **SELENIUM-FLOW**, Dispatch.
>
> Every sortie so far was flown from one cockpit. Claude Code reads resources,
> asks for progress, and was the client every decision in Chapter 2 was proved
> against. Then Dr K put a different airframe on the field: **VS Code Copilot**,
> flown by Sonnet, in another project.
>
> It flew — once it was told `?resources=off`. Until then the server looked
> half empty to it: the manual, the flow library and the session status were all
> published as resources, and Copilot's model has no way to read one. The
> progress bar worked first time. And typing `#selenium-flow` offered one thing,
> which turned out to be the `assert` tool.
>
> This chapter is about the cockpits we did not build for — what each one can
> actually reach, and what the server should say to the one it is talking to.

---

## Status: **BUILT, in one pull request** — opened and built 2026-09-16

Planned first, then built. Dr K answered five questions in the first pass (§F3.1
to §F3.5) and the sixth in the second (§F3.6), and added two rules of their own
making: **n8n reaches resources through `mcp-kb`, not through this server**, and
**nothing here keeps backwards compatibility while Dr K is its only user** —
which took `--stateless` with it (§F3.7). Part III is ticked against the build.

What it was planned from:

- **The Copilot sortie**, Dr K's report of it, above.
- **Research** into how VS Code implements MCP, how Claude Code reads
  resources, what the protocol says about directories, and what a good tool
  description is — recorded in Part I with its sources, because several of the
  decisions below rest on facts about other people's software that will change.
- **`mcp-kb`**, the sibling at `/projects/modules/mcp-kb`, which has already
  built the generic resource mirror this chapter is weighing (§F3.6).

---

## Part I — What the other cockpits can reach

### VS Code Copilot: resources are the user's, not the model's

Measured against VS Code's documentation and its issue tracker, September 2026:

- **Resources are a UI feature.** A user browses them (*MCP: Browse Resources*)
  or attaches one to a message (*Add Context → MCP Resources*). The agent is
  given **no tool to list or read a resource**. That is open as
  [microsoft/vscode#291004](https://github.com/microsoft/vscode/issues/291004),
  which reads the source to prove it — `listResources()` and `readResource()`
  are called only from UI code — and has no maintainer answer.
- So **everything this server publishes only as a resource is invisible to
  Copilot's model**: `skill://selenium-flow/SKILL.md`, `flow://flows`,
  `flow://schema`, `session://current`, `session://files`, `secret://secrets`.
  The mirror tools exist for exactly this client, and the default hid them. That
  is the whole of why `?resources=off` fixed the sortie.
- **Resource templates** are filled in a quick pick, and VS Code asks the
  server's **completions** for suggestions. Ours offer none.
- **Names** are shown as they are published. VS Code's guidance is title case
  (`Application Logs`); ours are Python function names (`flows_resource`).
- **`#` references tools, tool sets and context items.** An MCP server appears
  as a tool set under its own name. Nothing on the server side takes part in
  `#` beyond listing tools, so the `#selenium-flow` → `assert` oddity is almost
  certainly VS Code labelling the set by its first tool alphabetically. **Not
  confirmed**; see the open questions.
- **Tool annotations are used**: `title` is shown when a tool runs, and a tool
  marked `readOnlyHint` runs without a confirmation dialog.
- **Progress** renders the message, and a bar once a `total` is known (September
  2025). Confirmed on this server by Dr K.
- **More than 128 tools** across every enabled server and extension triggers
  *virtual tools*: VS Code hides groups behind `activate_*` stubs. We publish 21,
  so it is not near — but every mirror tool counts toward it.
- VS Code identifies itself in `clientInfo` as **`Visual Studio Code`** (seen in
  its own MCP trace logs, v1.105).

### Claude Code: three built-in tools read resources

Confirmed by reading the tool schemas this session has loaded:

| Tool | Does |
|---|---|
| `ListMcpResourcesTool(server?)` | `resources/list` across servers, each row tagged with its server |
| `ReadMcpResourceTool(server, uri)` | `resources/read` |
| `ReadMcpResourceDirTool(server, uri)` | `resources/directory/read` — one level, and only against a server that declared support |

So in Claude Code the model reads resources itself, and the mirror tools are
rightly hidden.

### `resources/directory/read` is a skills-extension method

It is **SEP-2640**, part of the *Skills over MCP* extension: a server declares
`capabilities.extensions["io.modelcontextprotocol/skills"].directoryRead: true`,
and a client must not call it otherwise. A directory is a resource with
`mimeType: inode/directory`, listed one level at a time, with pagination shaped
like `resources/list`. **FastMCP 4.0.x does not implement it**, which is why this
server publishes `skill://selenium-flow/_manifest` instead.

### What a good tool description is

- **Anthropic** ([Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents);
  [Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)):
  describe it as you would to a new hire — what it does, when to use it and when
  not, what each parameter means, the caveats — "at least 3–4 sentences, more if
  the tool is complex". Parameter names should be unambiguous, errors should say
  how to fix the call, and fewer, more capable tools beat many overlapping ones.
- **The one large study** ([arXiv 2602.14878](https://arxiv.org/html/2602.14878v1),
  856 MCP tools): 56% fail to state their purpose clearly. Descriptions augmented
  with every component raised task success by a median 5.85 points **and took
  67% more steps**, regressing in a sixth of cases; **compact descriptions with
  the high-impact components performed statistically the same**, and removing
  examples did not hurt.

The two agree on shape and disagree on nothing: a clear purpose, when to use it,
what the arguments mean, the caveat that bites. What neither supports is an
essay. **Ours total 3,027 words across 21 tools**; `open_session` and
`screenshot` are about 300 each, most of it teaching that belongs in the skill.

---

## Part II — The doctrine

### §F3.1 — Decision (Dr K's): the `resources` default is decided by who is calling; an explicit value wins

Today a client is assumed to read resources unless it says otherwise. Instead:

1. **`?resources=` or `X-MCP-Resources`, when given, decides.** Unchanged.
2. **Otherwise the identified client decides**, from one named table:
   `Visual Studio Code` (and its Insiders build) does not read resources.
3. **An unknown client defaults to reading them**, as now — the assumption stays
   that a client is spec-complete.

**Footguns, which the build has to test for:**

- **Where `clientInfo` lives depends on the protocol generation.** On
  `2025-xx` it arrives once, in `initialize`, and is held by the transport
  session; on `2026-07-28` it rides in every request's `_meta`. FastMCP exposes
  neither as a documented accessor; `apps.supported()` already probes the
  session for capabilities and is the precedent.
- **`--stateless` had no transport session**, so a legacy-protocol client served
  statelessly could not be identified. Moot: the flag is gone (§F3.7).
- **Clients cache their tool list.** A listing is decided per request, so the
  first `tools/list` must already see the identity — detection cannot depend on
  anything learned later in the session.

### §F3.2 — Decision (Dr K's): the instructions point each client at what it can read

The instructions every client receives say to read
`skill://selenium-flow/SKILL.md` first. Copilot cannot. A client that gets the
mirror tools is told to call the skill tool instead. FastMCP has `on_initialize`
and `on_discover` middleware hooks, so the text can vary per client; if neither
can see the identity at that moment, the fallback is one sentence naming both.

### §F3.3 — Decision (Dr K's): hunt down every stale description, and guard against the next

A regex sweep, for concepts earlier chapters removed, found:

- `selenium_flow_skill` still promises to explain "whether you must pass
  session_id" (§F2.12 removed it);
- **both prompts**, `build_flow` and `repair_flow`, still describe a *saved mode*
  in which you "never pass a `session_id`".

A regex only finds the words someone remembered. The build reads every tool,
resource and prompt description, and every refusal that names a tool, against the
current contract. Error text is included: a refusal said *"list_flows shows what
there is"* during this research, which goes stale the moment §F3.6 changes the
mirrors. **Guard:** extend `test_no_published_schema_carries_a_note_for_developers`
into a test over descriptions and prompts for the removed vocabulary.

### §F3.4 — Decision (Dr K's): resources get human names, and templates get completions

For the person browsing them in VS Code: `Current Session`, `Saved Flows`,
`Flow Document Schema`, `Kept Files`, `Secrets`, and one per skill file. Both
templates offer completions — `flow://flows/{name}` the caller's flow names,
`session://files/{name}` the caller's file names — answered for the caller who
asked, never across sessions.

**Verify first:** that FastMCP 4.0.x serves `completion/complete` for resource
templates, and that the handler can see the caller's session name.

### §F3.5 — Decision (Dr K's): tool descriptions are trimmed to what a model needs to call the tool

**The budget, from Part I:** one sentence of purpose; when to use it, and the
nearest tool to use instead; what any argument means that its schema cannot say;
the one caveat that bites. Most tools in 40–120 words. Anything that is teaching
— strategy, examples, why — moves to the skill, which the instructions point at.

**Measured, not guessed.** The word count per tool is recorded before and after,
and the published schemas stay byte-identical apart from `description`. A
description that loses a sentence a pilot relied on is the regression to watch;
the three pilot reports in Chapter 2 name several.

### §F3.6 — Decision (Dr K's): two generic mirrors replace seven specific ones, and every text names a URI

**Proposed:** a tool-only client gets `list_resources()` and `read_resource(uri)`,
exactly the vocabulary `mcp-kb` uses (`kubed/mcp_kb/mcp/tools.py`), instead of
`current_session`, `selenium_flow_skill`, `session_files`, `list_flows`,
`get_flow`, `flow_schema` and `list_secrets`.

**Would it work? Yes — proved before recommending it.** FastMCP's
`ResourcesAsTools` transform was added to this server and driven over HTTP by
two callers:

- `read_resource("session://current")` answered `alice` to alice and `bob` to
  bob: a read *inside* a tool call runs in the same request context, so the
  caller's session name survives;
- `read_resource("flow://flows/login")` returned alice's flow to her and refused
  bob with the ordinary "no flow called 'login'" — the middleware and scoping
  apply to the nested read;
- the listing was **17 rows, 5.5 KB**, templates included as `uri_template`.

**For it:**

- **Two tools where there were seven**, and the vocabulary is the protocol's: an
  agent that can drive `resources/read` already knows these.
- **One grammar across servers.** An agent given `mcp-kb` and this server learns
  `list_resources` / `read_resource` once.
- **Nothing to keep in step.** A resource added later is readable through the
  mirror the day it ships; today it needs its own mirror tool, and forgetting one
  is invisible to every test that runs in a resource-reading client.
- **Fewer tool tokens** for a client that needs mirrors, against VS Code's limit
  and everyone's context.

**The footguns, and what each needs:**

1. **Binary resources.** `session://files/{name}` is `application/octet-stream`.
   `ResourcesAsTools` base64-encodes binary into the tool result, so a model
   reading a downloaded PDF would put megabytes of text into its own context.
   **The mirror refuses binary** and answers with the file's name, size, type and
   HTTP link. That alone rules out the transform as-is: hand-written, as `mcp-kb`
   did, or a subclass.
2. **The MCP Apps shell is listed.** `ui://selenium-flow/component` is HTML for a
   host's iframe, not something to read. **The listing omits `ui://`.**
3. **Typed arguments become a URI to build.** `get_flow(name="login")` becomes
   `read_resource("flow://flows/login")`. A model can get a template wrong, so a
   miss must answer with the templates and a URI that would have worked — the
   §F2.16 rule that a refusal names the shape.
4. **Guidance moves from the tool list to the listing.** `list_secrets` carries
   141 words about how secrets are bound; under this proposal a model sees it only
   after calling `list_resources`. Resource descriptions have to carry what the
   mirror tools' descriptions carry now, and the instructions should name the
   three URIs a first session needs, so it costs no extra round trip.
5. **Name collisions in clients that do not namespace.** n8n's MCP Client Tool
   hands tool names to the agent as they are. An n8n agent wired to `mcp-kb`
   *and* this server would see two `read_resource` tools. VS Code and Claude Code
   prefix by server; **n8n has to be measured** before this ships, since it is
   the first client the mirrors were built for.
6. **One annotation for every read.** `session://current` asks the Grid;
   `skill://` reads packaged files. A single tool is annotated for the widest:
   read-only, open world.
7. **Fifty-one references to the old names** across 15 files — code, skill,
   prompts, the OpenAPI builder and error text. Each becomes stale in the same
   commit, which is §F3.3's guard earning its keep.
8. **Clients that cached the old tool list** keep calling `get_flow` after a
   rollout. Per `CONTRIBUTING.md`'s rule on changing shapes, the old names should
   keep working, unlisted, for a release.

**What this does not include: `read_resource_dir`.** `resources/directory/read`
is SEP-2640, gated behind the skills extension, and FastMCP does not serve it. A
mirror of a method the server does not implement would be invented vocabulary.
This server ships one skill with a `_manifest`; directories belong to `mcp-kb`,
whose whole catalogue is directories.

**And what it points at.** `mcp-kb` is meant to be *the* server for "read the
resource at this URI". Whether this server's skill should one day be served from
there — rather than embedded here, where it is guaranteed to describe this
version — is an open question, not a proposal.

**Recommendation, as written before it was answered:** adopt it, hand-written,
with footguns 1–4 and 8 designed in, and footgun 5 measured in n8n first.

**Answered, and wider than asked.** Dr K took the generic mirror and made it the
rule for all text: **every description, hint, error, prompt and skill page names
a resource by URI** — `session://current`, `flow://flows/{name}`,
`skill://selenium-flow/references/FLOWS.md` — so a client that reads resources
knows what to do with one, and a client that cannot is given the two tools that
take the same URIs. And two footguns were answered by decision rather than
design:

- **Footgun 5, n8n, is not this server's.** n8n keeps resources on here, so it is
  shown no mirror tools at all, and `mcp-kb` proxies this server's resources into
  its own catalogue; an n8n agent is told every URI is read through `mcp-kb`.
  There is one `read_resource` in that agent, and it is `mcp-kb`'s.
- **Footgun 8, cached tool lists, is not worth a release of aliases** (§F3.7).

**As built** (`mcp/mirror.py`): images read back as MCP image content; any other
binary is described — its type, size, and for a session file that
`session://files` carries its link — never base64; `ui://` is neither listed nor
readable; a URI that is not there is answered with the templates. `session_files`
survives only as the MCP App it also was, listed to a host that renders apps.
The OpenAPI spec marks each read endpoint `x-mcp-resource` with its URI, and the
wiki page for it shows the read.

### §F3.7 — Decision (Dr K's): no backwards compatibility while there is one user

This server, and `mcp-kb`, have one user. So a change that breaks a shape breaks
it and updates every reference in the same pull request: no deprecation window,
no retired name kept callable, no legacy mode kept for a deployment nobody runs.
`CONTRIBUTING.md`'s rule on changing an argument's shape is the policy for the
day there is a second user, and Dr K says when that is.

**`--stateless` is removed** under it. It dropped MCP transport sessions so more
than one replica could serve `/mcp` — and a transport session is where a legacy
client's `initialize`, and so its identity (§F3.1), is remembered. One replica
runs; the flag went.

---

## Part III — The plan

One pull request, built in this order because each step changes what the next
one reads. Every item was proved by breaking it on purpose and watching its test
fail.

### E22 — Who is calling (§F3.1, §F3.2)

- [x] `mcp/clients.py` reads `clientInfo` from `_meta` on `2026-07-28` and from
      the session's `initialize` on `2025`; `NO_RESOURCES` names VS Code.
- [x] `reads_resources()`: explicit value, else identified client, else true —
      tested on both protocol generations through the real client.
- [x] Instructions per client, answered on `initialize` and `server/discover`.
- [x] `--stateless` removed (§F3.7).

### E23 — Stale and long descriptions (§F3.3, §F3.5)

- [x] Every tool, resource and prompt description, and every refusal that named a
      retired tool, now names a URI. Also found stale: `keep_file` told a caller
      `upload_file(path=...)` where it means `kept=`; SKILL.md's rule 4 still
      described flat `xpath`/`css`; `build_flow` said to scope `outline` "with
      `css`".
- [x] **3,156 words across 24 tools became 1,578.** The longest are now `save_flow`
      and `outline`, around 120 each. `press_key`'s sixty key names moved to its
      refusal, which already listed them.
- [x] Guard: a test reads everything an agent is given from the running server —
      descriptions, both kinds of instructions, rendered prompts, every skill
      file — and fails on a removed name.

### E24 — Resources for people (§F3.4)

- [x] Names a person reads: `Current Session`, `Saved Flows`, `Saved Flow`,
      `Flow Document Schema`, `Session Files`, `Session File`, `Secrets`.
- [x] Completions for `flow://flows/{name}`, `session://files/{name}` and
      `repair_flow`'s `flow`, read from the caller's own listings.

### E25 — The generic mirror (§F3.6)

- [x] ~~Measure n8n with two servers exposing `read_resource`.~~ Answered by
      decision: n8n reads this server's resources through `mcp-kb`.
- [x] `list_resources` / `read_resource`: no `ui://`, images as images, other
      binary described, a miss names the templates.
- [x] Resource descriptions carry what the retired mirror tools said.
- [x] ~~Old mirror names callable for one release.~~ Removed outright (§F3.7);
      every reference updated.

### Carried from #38's last review

- [x] `write(secret=...)` beside an unrelated bad argument no longer also says
      "needs text".
- [x] Chapter 2's §F2.14 says what `outline` shipped.

### §F3.8 — Flying it: what the live check of #39 found

Everything #39 promised held against the deployed pod, read as Claude Code and
as VS Code — a real PNG came back an image, a real PDF was described in 233
characters. Three faults turned up beside it, none of them #39's, and all three
were built in the next pull request:

- **Saved files went missing, for two unrelated reasons**, measured on the Grid
  with Chrome 152. A PDF saved from a plain-http page was held as an *insecure
  download* — Chrome's own words, read off `chrome://downloads` — and never
  reached the store, while PNGs were not held; no feature flag or Safe Browsing
  preference releases it, and the insecure-content setting does. Separately, a
  page with no origin (`about:blank`, `data:`) is allowed exactly one download,
  and the automatic-downloads preference — which *is* applied, as
  `chrome://prefs-internals` shows — does not reach an opaque origin. A second
  tab would get round it, and **Dr K ruled that out: switching tabs is not this
  server's to do**, since it would also drop the session out of a frame. That
  case now fails with a sentence saying so. Firefox saved correctly everywhere.
- **A failed resource read quoted the Grid's internal URL.** FastMCP wraps a
  resource's exception verbatim before any middleware sees it; the rewrite and
  the log filter #38 built for tool calls now cover reads too.
- **`GET /secrets` was never in the OpenAPI spec**, so the one readable
  resource had no HTTP documentation and no wiki page.

---

## Open questions

1. ~~**§F3.6 — adopt the generic mirror?**~~ **Closed: yes, and every text names
   a URI.** n8n reads through `mcp-kb`.
2. **Why does `#selenium-flow` offer `assert`?** Believed to be VS Code labelling
   a tool set by its first tool. Worth one check in VS Code: does
   `#selenium-flow` in a prompt enable the whole server?
3. **Prompts in tool-only clients.** n8n cannot see `build_flow` or
   `repair_flow`. `mcp-kb` answers with `?prompts=off` and FastMCP's
   `PromptsAsTools`. VS Code does support prompts, as slash commands. Wanted
   here, or is a person always in a client that has them?
4. **Should this server's skill be served by `mcp-kb` one day?** Nearer now:
   `mcp-kb` will proxy this server's resources for n8n anyway, `skill://` among
   them.
5. **Which other clients belong in §F3.1's table?** Only VS Code is measured.
   ChatGPT, Cursor and n8n are candidates; none goes in without a measurement.
