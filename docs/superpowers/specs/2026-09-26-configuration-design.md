# Configuration: one setting, three spellings

Design record for the configuration file. Written 2026-09-26, before any code
moved. Planned from Dr K's brief of the same day.

**Rulings so far (Dr K, 2026-09-26):** pydantic-settings; the admin page's top
tabs are drawn on one Penpot page so they can be wired; a setting's source is a
pill, and its three names show when the row expands. This round lives in
`docs/superpowers/` and not in the saga.

**Status:** spec written and drawn. The Penpot file *Admin UI* holds the
drawing, in version *Configuration design, round 2 — dots, pills only, ⓘ tooltips,
wiki link*. Next: Dr K approves both, then the plan.

## Goal

- **Every setting can come from any of three places**: a YAML config file, an
  environment variable, or a CLI flag. A later place overrides an earlier one.
- **One naming rule translates between them**, so knowing one spelling gives you
  the other two.
- **The config file stays structural.** It has no templating and no `${…}`
  interpolation. It points at things by reference, and when a value could come
  from more than one kind of place, a discriminator key says which.
- **The config file can declare secrets**, merged over the ones read from the
  secrets directories. That means a secret's description and allowed sites can
  live in the config instead of in a ConfigMap written to a mounted file. The
  config can also define a whole new secret whose keys come from files or
  environment variables.
- **The admin UI gets a Settings tab**, after Secrets. It is a read-only view of
  every setting except the secret definitions, which the Secrets tab already
  shows.
- **The README stays high level.** The wiki gets the full breakdown: every
  setting in all three spellings.

## Non-goals

- **No hot reload.** Settings are read once, at boot. The secrets catalogue
  already re-reads its directories every 30 s; that does not change.
- **No writing settings from the UI.** The tab is read-only, like Secrets.
- **No templating or interpolation** anywhere in the file.
- **No backwards compatibility.** There is one user. Renamed environment
  variables are renamed outright, with no aliases or deprecation window.
- **No references for ordinary settings.** `auth.token: {file: …}` is not a
  thing: a sensitive setting takes a plain value in any layer, and the README
  says to set those in env. Only secret *keys* take references.
- **Nothing on the MCP surface.** No tool and no resource reads settings,
  because the admin surface is HTTP endpoints and the UI (AGENTS.md).

## Research that shaped it

| Source | What it contributed |
|---|---|
| **duploctl** (`argtype.py`, `args.py`) | Each setting is declared once, with its flag and env name beside it, and env is folded into argparse's default so `--help` shows the effective value. We avoid its two faults: hand-written env names, and a config file that is a fallback and not a merged layer (`controller.py`'s *"Known footgun"*). |
| **Mozilla convict / ts-convict** | Precedence **default < file < env < arg**. Each setting carries a `doc` and a `sensitive` flag. ts-convict has had no commits since 2022, so it is a pattern to follow, not a dependency. |
| **pydantic-settings 2.15.0** | 1,463 stars, 172 commits in the last year, released 2026-08-07. **Already installed**: `fastmcp` → `fastmcp-slim` requires `pydantic-settings>=2.0.0`, and the live image carries 2.15.0. We import it directly, so `pyproject.toml` declares it (the house rule from #45). |
| jsonargparse, dynaconf, confuse | Rejected. jsonargparse replaces argparse and names flags `--redis.max_connections`. dynaconf has no CLI source and supports `@format`/`@jinja` templating. confuse is YAML-first with `APP__` env names. |

What a prototype against pydantic-settings 2.15 established:

- `env_nested_delimiter="_"` with `env_nested_max_split=1` maps `REDIS_HOST` to
  `redis.host` and `SESSION_PAGE_LOAD_TIMEOUT` to `session.page_load_timeout`.
  Top-level `LOG_LEVEL` still works.
- Nested sources deep-merge per field. A `port` set in the file survives a
  `host` set in env.
- Its env source collects **every** variable that starts with a section name.
  The live pod carries Kubernetes service links such as `SELENIUM_FLOW_PORT`,
  `MCP_KB_PORT` and `N8N_PORT`. A future Service called `flow-ui` would inject
  `FLOW_UI_PORT`, which reads as `flow.ui_port`, and a strict model then
  refuses to boot. **The env source is therefore filtered to known fields.**
- Its CLI source names nested flags `--session.ttl`, adds a JSON flag per
  section (`--auth [JSON]`), and would expose `--secrets.entries`. **We generate
  our own argparse flags instead** (below).
- Silent by default: a missing YAML file and an unknown YAML key are both
  ignored. We guard both.
- A complex field (`list[str]`) is JSON-decoded from env, so
  `SECRETS_DIRS=/a:/b` fails unless the field is `NoDecode` with a splitting
  validator.

## The naming rule

A setting's **path** is its name. The other two spellings are derived from it
mechanically, and a test holds that for every setting:

| Config path | Env | Flag |
|---|---|---|
| `section.key_name` | `SECTION_KEY_NAME` | `--section-key-name` |
| `key_name` (top level) | `KEY_NAME` | `--key-name` |

Three constraints follow from the rule:

- **A section name has no underscore.** The first `_` in an env name separates
  the section from the key.
- **A section name is not a prefix Kubernetes or the shell already uses.** So
  `browser` is out, because code-server and many shells set `BROWSER`, which
  would be read as the whole section. `mcp` is out because of `MCP_KB_PORT`.
  `selenium` is out because of `SELENIUM_FLOW_PORT` and
  `SELENIUM_GRID_SELENIUM_HUB_PORT`.
- **Env names are matched case-insensitively.** This is pydantic-settings'
  default and is kept.

## The settings

Every setting the server reads today, in its new place. **Bold** names changed.

| Path | Env | Flag | Default | Sensitive |
|---|---|---|---|---|
| `config_file` | `CONFIG_FILE` | `--config-file` | unset | |
| `transport` | `TRANSPORT` | `--transport` | `http` | |
| `host` | `HOST` | `--host` | `0.0.0.0` | |
| `port` | `PORT` | `--port` | `8000` | |
| `log_level` | `LOG_LEVEL` | `--log-level` | `INFO` | |
| `route_prefix` | `ROUTE_PREFIX` | `--route-prefix` | `/` | |
| `public_base_url` | `PUBLIC_BASE_URL` | `--public-base-url` | unset | |
| **`auth.token`** | **`AUTH_TOKEN`** | `--auth-token` | unset | yes |
| `grid.url` | `GRID_URL` | `--grid-url` | the in-cluster hub | |
| `grid.console_url` | `GRID_CONSOLE_URL` | `--grid-console-url` | `/` | |
| `session.store` | `SESSION_STORE` | `--session-store` | derived (below) | |
| `session.ttl` | `SESSION_TTL` | `--session-ttl` | `86400` | |
| **`session.browser`** | **`SESSION_BROWSER`** | `--session-browser` | unset (Chrome) | |
| **`session.width`** | **`SESSION_WIDTH`** | `--session-width` | unset | |
| **`session.height`** | **`SESSION_HEIGHT`** | `--session-height` | unset | |
| **`session.page_load_timeout`** | **`SESSION_PAGE_LOAD_TIMEOUT`** | `--session-page-load-timeout` | unset | |
| **`session.script_timeout`** | **`SESSION_SCRIPT_TIMEOUT`** | `--session-script-timeout` | unset | |
| `redis.url` | `REDIS_URL` | `--redis-url` | unset | yes |
| `redis.host` | `REDIS_HOST` | `--redis-host` | `localhost` | |
| `redis.port` | `REDIS_PORT` | `--redis-port` | `6379` | |
| `redis.db` | `REDIS_DB` | `--redis-db` | `0` | |
| `redis.username` | `REDIS_USERNAME` | `--redis-username` | unset | |
| `redis.password` | `REDIS_PASSWORD` | `--redis-password` | unset | yes |
| `redis.ssl` | `REDIS_SSL` | `--redis-ssl` | `false` | |
| `redis.prefix` | `REDIS_PREFIX` | `--redis-prefix` | `selenium-flow:session:` | |
| `flow.data_dir` | `FLOW_DATA_DIR` | `--flow-data-dir` | unset (flows off) | |
| `secrets.dirs` | `SECRETS_DIRS` | `--secrets-dirs` | unset | |
| `secrets.entries` | — | — | none | config file only |
| `skill.enabled` | `SKILL_ENABLED` | `--skill-enabled` | `true` | |
| `apps.enabled` | `APPS_ENABLED` | `--apps-enabled` | `true` | |

Notes on the table:

- **Renames.** `MCP_AUTH_TOKEN` becomes `AUTH_TOKEN`. An `mcp` section is ruled
  out, and `auth` is also where the flag `--auth-token` already pointed.
  `DEFAULT_BROWSER`, `WINDOW_WIDTH`, `WINDOW_HEIGHT`, `PAGE_LOAD_TIMEOUT` and
  `SCRIPT_TIMEOUT` move under `session`, because they are the defaults a new
  session opens with. `session.browser` keeps the reason `DEFAULT_BROWSER` was
  not called `BROWSER`: as a *section* name, `browser` would swallow the Unix
  `BROWSER` variable.
- **Flag renames.** `--no-skill` and `--no-apps` become `--skill-enabled false`
  and `--apps-enabled false`. Every boolean flag takes a value
  (`true|false|1|0|yes|no|on|off`), so the flag rule has no exceptions.
- **`session.store` stays derived.** Unset means `redis` when `redis.url` or
  `redis.host` was set *by anything other than its default*, and `memory`
  otherwise. That is today's rule, and it is why provenance is known before
  validation (below).
- **`secrets.dirs`** is a list. In YAML it is written as a list. In env and on
  the command line it is split on `os.pathsep`, as today.
- **Defaults come from one place.** The table above replaces the `DEFAULT_*`
  constants that `main.py`, `store.py`, `pointer.py`, `admin.py` and
  `settings.py` each read from `os.environ` today. The README's `SESSION_TTL`
  default of `3600` has already drifted from the code's `86400`. The generated
  wiki page cannot drift that way.
- **The client-default cascade does not change.** `session.*` replaces the
  "server default (env)" floor in `session/settings.py`, and query parameters
  and headers still override it.

## Precedence and loading

```
defaults  <  config file  <  environment  <  command line
```

The order is convict's. Loading happens once, in `main()`:

1. **Parse the command line** with a parser generated from the schema. Every
   leaf gets one flag, `default=argparse.SUPPRESS`, so the namespace holds
   *only* what was typed. Help text is the field's `description`, plus
   `(env: SESSION_TTL · config: session.ttl · default: 86400)`, as duploctl
   does. `secrets.entries` gets no flag.
2. **Pick the config file.** `--config-file`, else `CONFIG_FILE`, else none.
   **There is no default location**, the rule `flow.data_dir` already follows.
   A path that was named and does not exist, cannot be read, or does not parse
   as a YAML mapping **stops the boot** with the path and the reason.
3. **Build `Settings`**, a `BaseSettings` with nested section models, through
   `settings_customise_sources`, highest priority first: `init_settings` (the
   typed flags), the filtered env source, then `YamlConfigSettingsSource`. The
   file path is passed in and never read from a module global.
4. **Validation stops the boot on a bad file.** Every section model is
   `extra="forbid"`, so an unknown key in the YAML is an error that names it.
   Env does not get that strictness: the filtered env source drops names that
   match no field, because the environment is shared with Kubernetes and the
   shell, and the file is not.
5. **Provenance is recorded per leaf.** Each source is called on its own and
   gives a dict, and the flags give the namespace. For each leaf, the highest
   source that set it is its source: `arg`, `env`, `config` or `default`. That
   is what the Settings tab shows, and `session.store`'s derivation reads it
   too.

This is the same rule §F4.12 applied to Redis: a server that cannot start is
restarted until it is fixed, while one that started on a misread config is
never corrected.

### Where settings go after loading

`main()` builds one `Settings` and hands it to `SeleniumMCP(settings)`.
Every `from_env(env)` becomes a `from_settings(section)`: in `store.py`,
`pointer.py`, `library.py`, `secrets.py`, `apps.py`, `skill.py`,
`settings.py`, and the `GRID_CONSOLE_URL` read in `admin.py`.
**`os.environ` is read in exactly two places afterwards**: the settings loader,
and the secret binding path when a key is `{env: …}`. A test greps the package
to hold that.

The constructor's keyword arguments (`grid_url=`, `auth_token=`, and so on)
go too, and the tests build a `Settings` instead. With no backwards
compatibility to keep, one way in is better than two.

## Secrets in the config

`secrets.entries` exists **only in the config file**. Env and flags cannot set
it, because it is structure, not a value. The filtered env source drops a
`SECRETS_ENTRIES` variable as it drops any unknown name, and there is no flag.

```yaml
secrets:
  dirs: [/secrets]

  entries:
    # Merges over /secrets/grafana, which an LDAP module mounts. Only the
    # policy is written here; the keys still come from the directory.
    grafana:
      description: The selenium LDAP service account, a Grafana Viewer
      allowed_urls: [https://grafana.example.com]

    # A whole secret defined here, nothing on disk. Each key says where its
    # value lives: exactly one of file, env or value.
    admin:
      description: This server's own admin token, so a flow can sign in
      allowed_urls: [https://selenium.example.com]
      keys:
        token: {env: AUTH_TOKEN}

    the-internet:
      description: Public demo login for the-internet.herokuapp.com
      allowed_urls: [https://the-internet.herokuapp.com]
      keys:
        username: {env: THE_INTERNET_USERNAME}
        password: {file: /run/secrets/the-internet/password}
```

### The shape

- An **entry** has `description`, `allowed_urls` and `keys`. All three are
  optional, and no other field is allowed.
- A **key reference** is exactly one of `{file: <path>}`, `{env: <NAME>}` or
  `{value: <string>}`. The key present *is* the discriminator. A second key, or
  none, is a validation error that stops the boot. pydantic would otherwise
  pick the first union member that fits and ignore the rest, so each member is
  `extra="forbid"`.
- An **entry's name** follows `valid_name`, the same rule a secret directory's
  name follows.
- **`allowed_urls`** entries are bare origins, checked by `declared_origin`.
  In a file a bad line is published and makes the secret unusable. **In config
  it stops the boot**, because config is validated where it is read.
  `allowed_urls: []` is a declared leash that allows nowhere, which is today's
  meaning of an `_allowed_urls` file that parses to nothing.

### The merge

**The collected secrets come first, and the config merges over them.**
The filesystem sources resolve as today (first directory wins). Then each
config entry is applied:

| Field | If the config sets it | If it does not |
|---|---|---|
| `description` | replaces `_description` | the directory's, if any |
| `allowed_urls` | replaces `_allowed_urls` **entirely**, rejected lines included | the directory's leash, if any |
| `keys` | added; a key with the same name as a file **replaces** that file for this key | the directory's keys |

- A name with no directory is a **config-only secret**. A name with both is a
  **merged** secret.
- **Secrets are on** when `secrets.dirs` or `secrets.entries` is non-empty.
  Today only the directories count.
- **The value is still read at the moment of binding** and never cached. For
  `{env: …}` that is `os.environ` at bind time; for `{file: …}` it is the
  file, read then. `bind()` is unchanged. What changes is `value(name, key)`,
  which asks the entry's owner of *that key*: the config reference or the
  directory.
- The catalogue's one-snapshot rule (§F1.23) still holds. The snapshot now
  records an owner per key, not per secret, so policy and value can never
  come from two different secrets.

### What the listing publishes

Each entry keeps every field it has today. `source` becomes `filesystem`,
`config` or `filesystem+config`, and `location` names the directory, the
config file, or both. Three fields are new:

- **`key_sources`**: for each key, `file`, `env`, `value` or `filesystem`, plus
  the env name or path where there is one. **Never a value.** An env name and
  a path are not secret, and "why is this key wrong" is otherwise
  unanswerable.
- **`keys_unresolved`**: keys whose reference cannot be read right now (the env
  variable is unset, or the file is missing). The check is a presence check
  when the listing is built. It never reads the value into the listing. Such
  a secret stays in the catalogue, because a file can appear later (Kubernetes
  projects secrets asynchronously). Binding that key refuses with the reason.
- **`inline_keys`**: keys whose value is written in the config itself
  (`{value: …}`). The Secrets tab marks the secret with a warn pill. The
  README recommends `file:` or `env:`.

### Why not have `{value: …}` at all

Dr K's brief allows raw values and asks that the README recommend against
them. They exist for a laptop and a demo site. They are `SecretStr` from the
moment they are parsed, so they never reach a log line, a listing, a repr or
the Settings tab.

## The admin API

**`GET /admin/settings`** is token-gated through `http/auth.py` like every
other admin route. It has no tool and no resource.

```json
{
  "config_file": "/etc/selenium-flow/config.yaml",
  "sections": [
    {
      "name": "session",
      "description": "How sessions are kept, and how new browsers open.",
      "settings": [
        {
          "path": "session.ttl",
          "env": "SESSION_TTL",
          "flag": "--session-ttl",
          "description": "Seconds a session is kept after its last use.",
          "value": 86400,
          "default": 86400,
          "source": "config",
          "sensitive": false,
          "set": true
        }
      ]
    }
  ]
}
```

- **Sensitive settings** (`auth.token`, `redis.password`, `redis.url`) carry
  `value: null` and `set: true|false`. Nothing else about them is published.
  `redis.url` is sensitive because it can carry a password.
- **`secrets.entries` is not in the payload.** The Secrets tab shows it.
  `secrets.dirs` is, because it is a setting.
- The top-level section is called `server` in the payload and the UI. It
  covers `host`, `port`, `transport` and the rest, which have no section in
  their paths.

## The Settings tab

A top-level tab after **Secrets**, at `#/settings`, in the order Sessions ·
Secrets · Settings · Grid console.

- **Header:** "Settings", one line — *How this server was started.
  Read-only.* — and a link, *Every setting is described on the wiki →*, to
  the live wiki's Configuration page. There is no loaded-from or no-config
  line: the `config_file` row already says which file, if any.
- **A legend** of the four source pills, in precedence order
  (`default config env arg`), right-aligned above the cards. No words.
- **One card per section**, in the schema's order, each with a short
  description under the title (one clause, not a paragraph).
- **One row per setting.** On the left, the path in mono with an **ⓘ**
  beside it. In the middle, the value: `—` if unset. A sensitive value is
  `●●●●` when set and blank when not; no words. On the right, a **source
  pill**: `default`, `config`, `env` or `arg`.
- **Hovering the ⓘ shows a tooltip**: the setting's description, one short
  sentence ("Seconds a session is kept after its last use."). The schema's
  `Field(description=…)` is written to that length, because the same text
  is the CLI help and the wiki's table cell.
- **A click on a row expands it.** It shows the three spellings (config path,
  env, flag) as copyable mono lines, and the default when the value differs
  from it. A sensitive row shows only the three spellings. A second click
  collapses it. Any number of rows can be open. The expand state is not in
  the hash.

**The Secrets tab changes** only where the config shows up:

- The **from** fact names every source, e.g. `filesystem · /secrets +
  config · /etc/…/config.yaml`.
- A **key pill** from the config carries its source as a suffix: `token · env
  AUTH_TOKEN`, `password · file`, `username · value`. A key from a directory
  stays a bare name.
- A **warn pill** `inline value` appears when `inline_keys` is non-empty, and
  `key unresolved` when `keys_unresolved` is, with the reason in the card's
  error line.

## Documentation

- **README**: the `⚙️ Configuration` table is replaced by the naming rule, the
  precedence line, a ten-line config example, and a link to the wiki page. It
  recommends `file:` or `env:` over `value:`. It advertises; it does not
  explain.
- **Wiki `Configuration.md`, generated** by `scripts/generate_wiki.py` from
  the schema: one table per section, every setting with its path, env, flag,
  default and description. It also covers the precedence line, the config
  file's shape and selection, and a worked example of one value in each of
  the three places. `tests/test_wiki.py` fails when it is stale, as it does
  for the action pages.
- **Wiki `Secrets.md`**: a hand-written section, *Secrets in the config*. It
  covers the merge table, the three reference kinds, and why `value:` is last.
- **Wiki `Deployment.md`**: its configuration table is replaced by a link to
  Configuration. The Kubernetes section shows the config ConfigMap and
  `enableServiceLinks: false`.
- **`skills/selenium-flow/references/CONFIGURATION.md`**: the renamed variables,
  and the config file.
- **AGENTS.md**: a short *Configuration* section with the naming rule, the
  three constraints, the two places `os.environ` may be read, and why env is
  lenient while the file is strict.
- **CHANGELOG `[Unreleased]`**: one line each for the config file, config
  secrets, the Settings tab, and the renamed variables (breaking).

## Penpot

The file **Admin UI**, drawn before any code, as usual. Dr K's ruling of
2026-09-26 changes the file's shape. The top tabs were dead on every page,
because a Penpot 2.17.2 link cannot leave its page and each tab lived on a
different one.

- **The page `Admin`** holds every top-level view: sign in, the sessions list,
  Secrets, **Settings** and Grid console. It is the old `Sessions` page,
  renamed. The Secrets board was carried in as component → instance → detach,
  since a shape cannot move between pages, and the `Secrets` page is deleted.
  Every top tab on every board is wired to its board, the logo goes to the
  sessions list, Sign out goes to sign in, and the current tab starts over.
- **Session · Files** and **Session · Flows** stay as they are. Their nav
  gains the Settings tab through the component. A session card into Files is
  still drawn and not wired, and each guide board says the top tabs lead to
  Admin.
- **Settings is drawn as stories**, each playable end to end:
  - `settings`, with every row collapsed;
  - `settings-redis-db`, an ordinary row expanded, with its default shown
    because it differs;
  - `settings-auth-token`, a sensitive row expanded: its three names and
    nothing else;
  - `settings-no-config`, its own flow: the same tab with every value a
    default or from env.

  The two rows click open and closed. Every other row is drawn collapsed.
  **Every ⓘ hovers**: 29 shared tooltip boards (`tip / <path>`) open as
  overlays on mouse-enter and close on mouse-leave, on all four boards.
- **Source pills follow precedence in weight**: `default` is a faint outline,
  `config` an accent outline, `env` accent-filled, and `arg` ink-filled. The
  strongest source looks strongest. The legend is the four pills, in that
  order, with no words.
- **Secrets gains the config states**:
  - `admin`, config-only with an env key;
  - `grafana`, a directory merged with config;
  - `the-internet`, env keys;
  - `demo-site`, an inline value;
  - `github`, an unresolved key.

  Key pills carry a suffix only when the key comes from the config (`token ·
  env AUTH_TOKEN`, `password · value`). A key read from a directory stays a
  bare name, as today.
- **Components:** `nav / settings`, a Settings tab in every `nav`,
  `setting-row / collapsed|expanded` (each with an `info` instance beside the
  path), `source-pill / default|config|env|arg`, `info` and `tooltip`. A section card is a plain board of row instances,
  not a component, because its row count varies.
- **Checked mechanically before review**, as §F4.11 requires: no main component
  carries a link, no dead link, every board reachable from the page's start,
  **no dead ends**, no flow nobody named, and `File.validate()` clean. A
  version is saved before the restructure and one for review.

## The cluster install

`apps/selenium/components/mcp` in the cluster repo is updated so the live
deployment runs on the config, with a value in each of the three places. That
makes it an example as well as a deployment.

- **`config.yaml`** (new), rendered by a `configMapGenerator` into
  `selenium-flow-config` and mounted at `/etc/selenium-flow/config.yaml`. It
  holds `session` (store, ttl), `redis` (host, db, prefix), `flow.data_dir`,
  `secrets.dirs` and `secrets.entries`.
- **`mcp.env`** keeps `CONFIG_FILE`, `GRID_URL`, `GRID_CONSOLE_URL` and
  `PUBLIC_BASE_URL`: what is specific to where the pod runs.
- **Container `args`** carry `--log-level INFO` and `--port 8000`, to show the
  third place.
- **`AUTH_TOKEN`** comes from `selenium-flow-auth` through
  `env.valueFrom.secretKeyRef`, on key `MCP_AUTH_TOKEN`. The Secret keeps its
  key name because codeserver reads it by that name
  (`apps/codeserver/components/env/secret.yaml`).
- **The admin secret** becomes a config entry with `token: {env: AUTH_TOKEN}`.
  Its projected volume and `selenium-flow-secret-admin-meta` go.
- **The grafana secret** keeps its directory, projected from the LDAP-owned
  Secret. Its description and leash move into `config.yaml`, and
  `selenium-flow-secret-grafana-meta` goes.
- **The the-internet secret** keeps its `secretGenerator`, but loses its `_*`
  literals. It is mounted as env (`THE_INTERNET_USERNAME` /
  `THE_INTERNET_PASSWORD`) and defined in `config.yaml` with `{env: …}` keys,
  to show secrets arriving as environment variables.
- **`enableServiceLinks: false`** on the pod, as a second guard against
  injected `*_PORT` variables.

## Testing

Unit tests, TDD. The integration suite stays at its current flows (AGENTS.md:
*less is more*). One new integration flow is added only if the Settings tab
can break in a way no unit test sees, and that is argued in the plan.

- **The naming rule**: for every leaf in the schema, `env == path.upper().replace(".", "_")`
  and `flag == "--" + path.replace(".", "-").replace("_", "-")`. No section
  name contains `_`, and none is `browser`, `mcp` or `selenium`.
- **Precedence**, per leaf: default < file < env < arg, including a partial
  section (a file `redis.port` survives an env `REDIS_HOST`).
- **Provenance** matches precedence for every source.
- **Strictness**: an unknown YAML key, a missing named config file, a
  non-mapping file, a bad origin in `allowed_urls`, and a reference with two
  keys or none all stop the boot with a message that names the problem.
  `FLOW_UI_PORT`, `MCP_KB_PORT` and `BROWSER` in the environment do not.
- **`os.environ` read only in the two places**: a grep over the package.
- **The merge**: description and leash replaced, keys added and overridden,
  config-only secrets, secrets on with only entries, `key_sources`,
  `keys_unresolved`, `inline_keys`. `bind` reads an `{env}` key at call time,
  and a key changed after the snapshot still resolves to the same owner.
- **Never a value**: no inline value, env value or file value appears in the
  secrets listing, the settings payload, a log line, or `repr(Settings)`.
  Proven by breaking it on purpose first.
- **`GET /admin/settings`**: 401 without the token, every section, sensitive
  values withheld, `secrets.entries` absent.
- **UI (vitest)**: the Settings pane renders sections, source pills, expand
  and collapse, the sensitive row and the no-config state. The Secrets pane
  shows key sources and the two new warn pills. The router handles
  `#/settings`.
- **The wiki page** is regenerated and `test_wiki.py` guards it.

## Open for the plan

- The pydantic-settings floor. `env_nested_max_split` and `NoDecode` set the
  minimum version, to be checked against the changelog.
- Whether the generated help text is produced from `Field(description=…)`
  alone, or also carries an example.
