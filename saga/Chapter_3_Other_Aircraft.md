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

## Status: **OPEN — planning, answered in part** — opened 2026-09-16

Nothing is built yet. Dr K answered five questions in the first pass (§F3.1 to
§F3.5); §F3.6 is the one this chapter exists to settle, and it is *recommended*
until Dr K answers it.

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
- **`--stateless` has no transport session.** A legacy-protocol client served
  statelessly has no `initialize` to remember, so detection finds nothing and
  falls back to the default. That must fail toward the current behaviour, never
  toward an error.
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

### §F3.6 — Decision (recommended): two generic mirrors replace seven specific ones

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

**Recommendation:** adopt it, hand-written, with footguns 1–4 and 8 designed in,
and footgun 5 measured in n8n first. If n8n cannot tell two `read_resource` tools
apart, the choice becomes prefixing both servers' mirrors or keeping specific
mirrors here, and that is Dr K's.

---

## Part III — The plan

One pull request, in this order, because each step changes what the next one
reads:

### E22 — Who is calling (§F3.1, §F3.2)

- [ ] Read `clientInfo` on both protocol generations; name the table of clients
      that do not read resources.
- [ ] `client_reads_resources()`: explicit value, else identified client, else
      true. Tested per generation, and under `--stateless`.
- [ ] Instructions per client, with the both-sentence fallback.

### E23 — Stale and long descriptions (§F3.3, §F3.5)

- [ ] Read every tool, resource and prompt description and every refusal that
      names a tool; fix `selenium_flow_skill`, `build_flow`, `repair_flow`.
- [ ] Trim tool descriptions to the budget; teaching moves to the skill. Record
      words per tool before and after.
- [ ] Guard: removed vocabulary never appears in a description or prompt.

### E24 — Resources for people (§F3.4)

- [ ] Title-case names on every resource.
- [ ] Completions for both templates, per caller.

### E25 — The generic mirror (§F3.6), **if Dr K signs it off**

- [ ] Measure n8n with two servers exposing `read_resource`.
- [ ] `list_resources` / `read_resource`: no `ui://`, binary refused with a link,
      a miss names the templates.
- [ ] Resource descriptions carry what the retired mirror tools said.
- [ ] Old mirror names callable and unlisted for one release; every reference
      updated.

### Carried from #38's last review

Small and real, recorded here rather than in the thread:

- [ ] `write(secret=...)` beside an unrelated bad argument also says "needs
      text": `argument_problems` passes an empty `bound`, so it cannot see the
      secret the call already carries.
- [ ] Chapter 2's §F2.14 still shows `outline` answering `selector: {"css": …}`.
      What shipped is `css` or `xpath` on the entry; the record should say so.

---

## Open questions

1. **§F3.6 — adopt the generic mirror?** Recommended, subject to the n8n
   measurement.
2. **Why does `#selenium-flow` offer `assert`?** Believed to be VS Code labelling
   a tool set by its first tool. Worth one check in VS Code: does
   `#selenium-flow` in a prompt enable the whole server?
3. **Prompts in tool-only clients.** n8n cannot see `build_flow` or
   `repair_flow`. `mcp-kb` answers with `?prompts=off` and FastMCP's
   `PromptsAsTools`. VS Code does support prompts, as slash commands. Wanted
   here, or is a person always in a client that has them?
4. **Should this server's skill be served by `mcp-kb` one day?**
5. **Which other clients belong in §F3.1's table?** Only VS Code is measured.
   ChatGPT, Cursor and n8n are candidates; none goes in without a measurement.
