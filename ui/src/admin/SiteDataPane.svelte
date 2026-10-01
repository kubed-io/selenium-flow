<script lang="ts">
  import { tick, untrack } from 'svelte'
  import { ago } from '../lib/format'
  import type { SiteCookie, SiteDetail, SiteRow, SiteStorage } from '../lib/types'
  import { sessionPath, type Api } from './api'
  import { folds } from './folds.svelte'
  import type { ModalSpec } from './modal'
  import Section from './Section.svelte'
  import type { SessionModel } from './session.svelte'

  let { m, api, ask, refuseIfGone, isGone, site, hidden }: {
    m: SessionModel
    api: Api
    ask: <T>(spec: ModalSpec<T>) => void
    refuseIfGone: () => void
    isGone: () => boolean
    /** A host linked from History: opened and brought into view. */
    site?: string
    hidden: boolean
  } = $props()

  const data = $derived(m.siteData)
  // Ends in `Section`: Section derives its body's id by replacing that.
  const idOf = (r: SiteRow) => 'site-' + m.key + ':' + r.site + 'Section'

  // The first row opens, the rest stay shut — once; after that the fold is
  // the reader's.
  $effect.pre(() => {
    const sites = data?.sites ?? []
    untrack(() => sites.forEach((r, i) => { folds[idOf(r)] ??= i === 0 }))
  })

  // A link from History opens its host and brings it into view, once per link.
  let linked: string | undefined
  $effect.pre(() => {
    // No host in the route: the next link, even to the same host, is a new one.
    if (!site) linked = undefined
    const r = (data?.sites ?? []).find((x) => x.site === site)
    if (!r || site === linked) return
    linked = site
    untrack(() => { folds[idOf(r)] = true })
    void tick().then(() => document.getElementById(idOf(r))?.scrollIntoView?.({ block: 'start' }))
  })

  const plural = (n: number, one: string, many = one + 's') => n + ' ' + (n === 1 ? one : many)
  const sum = (r: SiteRow, k: 'local_storage' | 'session_storage') => r.storage.reduce((n, e) => n + e[k], 0)
  // A cookie-only host reads "3 cookies"; one with storage gives every count.
  const counts = (r: SiteRow) => [
    plural(r.cookies, 'cookie'),
    ...(r.storage.length ? [sum(r, 'local_storage') + ' local', sum(r, 'session_storage') + ' session'] : []),
  ].join(' · ')
  // The origin when the host has one, the host when it has none or several.
  const titleOf = (r: SiteRow) => (r.storage.length === 1 ? r.storage[0].origin : r.site)

  const keysOf = (d: SiteDetail | undefined) =>
    [...new Set((d?.storage ?? []).flatMap((e) => [...Object.keys(e.local_storage), ...Object.keys(e.session_storage)]))]
  // A host with no storage still shows both groups, as "none".
  const groups = (d: SiteDetail): SiteStorage[] =>
    d.storage.length ? d.storage : [{ origin: '', local_storage: {}, session_storage: {} }]

  const expires = (c: SiteCookie) => (c.expiry ? new Date(c.expiry * 1000).toLocaleDateString() : 'session')

  interface Forget { origin: string; goes: string[]; stays: { key: string; label: string }[] }

  function forget(r: SiteRow) {
    const d: SiteDetail | undefined = data?.details[r.site]
    // Which cookies go, and which stay, is the server's call (Forget's own
    // rule); each staying cookie arrives with its own domain and path.
    ask<Forget>({
      title: 'Forget site data',
      body: forgetBody,
      data: {
        origin: titleOf(r),
        goes: [...(d?.own_cookies ?? []), ...keysOf(d)],
        stays: (d?.kept_shared ?? []).map((c) => ({
          key: [c.name, c.domain, c.path].join('|'),
          label: c.name + ', shared with ' + c.domain + (c.path && c.path !== '/' ? ' ' + c.path : ''),
        })),
      },
      confirm: 'Forget', danger: true,
      onconfirm: async () => {
        refuseIfGone()
        await api(sessionPath(m.key, '/site-data/' + encodeURIComponent(r.site)), 'DELETE')
        // History's saved pills read this snapshot too.
        if (!isGone()) { void m.loadSiteData(); void m.loadHistory() }
      },
    })
  }

  function clear() {
    ask<string[]>({
      title: 'Clear site data',
      body: clearBody,
      data: (data?.sites ?? []).map((r) => r.site),
      confirm: 'Clear', danger: true,
      onconfirm: async () => {
        refuseIfGone()
        await api(sessionPath(m.key, '/site-data'), 'DELETE')
        if (!isGone()) { void m.loadSiteData(); void m.loadHistory() }
      },
    })
  }
</script>

{#snippet forgetBody(f: Forget)}
  <p>{f.origin} — a reopened browser comes back signed out here.</p>
  <div class="pairs fate-list">
    {#if f.goes.length}<div class="pair"><span class="pk">goes</span><span class="pv">{f.goes.join(' · ')}</span></div>{/if}
    {#each f.stays as s (s.key)}<div class="pair"><span class="pk">stays</span><span class="pv">{s.label}</span></div>{/each}
  </div>
{/snippet}

{#snippet clearBody(hosts: string[])}
  <p>{plural(hosts.length, 'site')} — a reopened browser comes back signed out.</p>
  <div class="pairs fate-list">
    <div class="pair"><span class="pk">goes</span><span class="pv">{hosts.join(' · ')}</span></div>
  </div>
{/snippet}

<!-- Which origin a group is, only when the host has more than one. -->
{#snippet from(d: SiteDetail, e: SiteStorage)}
  {#if d.storage.length > 1}<span class="origin">{' · ' + e.origin}</span>{/if}
{/snippet}

{#snippet kv(entries: [string, string][])}
  {#each entries as [k, v] (k)}
    <div class="line"><code class="key">{k}</code><span class="value clip" title={v}>{v}</span></div>
  {:else}
    <div class="line"><span class="value muted">none</span></div>
  {/each}
{/snippet}

<div id="paneSiteData" {hidden}>
  {#if m.siteDataError}
    <div class="empty error">{m.siteDataError}</div>
  {:else if !data}
    <div class="empty">Loading…</div>
  {:else if !data.sites.length}
    <p class="small muted">Nothing saved — an agent calls <code>save_site_data</code> after signing in.</p>
  {:else}
    <div class="row bar">
      {#if data.saved_at}<span class="pill">saved {ago(data.saved_at * 1000)}</span>{/if}
      <span class="grow"></span>
      <button id="clearSiteData" class="danger" onclick={clear}>Clear</button>
    </div>
    {#each data.sites as r (r.site)}
      {@const d = data.details[r.site]}
      <!-- Remounted when it becomes the linked row: a Section reads its fold
           once, at mount. -->
      {#key r.site === site}
        <Section id={idOf(r)} title={titleOf(r)}>
          {#snippet summary()}<span class="small muted">{counts(r)}</span>{/snippet}
          {#snippet actions()}<button class="danger" onclick={() => forget(r)}>Forget</button>{/snippet}
          {#if d}
            <h3>Cookies</h3>
            {#each d.cookies as c (c.name + c.domain + c.path)}
              <div class="line cookie">
                <code class="key">{c.name}</code>
                <span class="value clip" title={c.value}>{c.value}</span>
                <span class="small muted">{c.domain} · {expires(c)}</span>
                <span class="flags">
                  {#if c.http_only}<span class="pill">httpOnly</span>{/if}
                  {#if c.secure}<span class="pill">secure</span>{/if}
                  {#if c.shared}<span class="pill shared">shared</span>{/if}
                </span>
              </div>
            {:else}
              <div class="line"><span class="value muted">none</span></div>
            {/each}
            {#each groups(d) as e (e.origin)}
              <h3>Local storage{@render from(d, e)}</h3>
              {@render kv(Object.entries(e.local_storage))}
              <h3>Session storage{@render from(d, e)}</h3>
              {@render kv(Object.entries(e.session_storage))}
            {/each}
          {/if}
        </Section>
      {/key}
    {/each}
  {/if}
</div>

<style>
  .bar { margin-bottom: 12px; }
  h3 { margin: 14px 0 4px; font-size: 11px; font-weight: 400; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }
  .line { display: grid; grid-template-columns: 240px 1fr auto; align-items: center; gap: 8px; padding: 6px 0; border-top: 1px solid var(--line); }
  .line.cookie { grid-template-columns: 240px 1fr auto auto; }
  h3 .origin { text-transform: none; letter-spacing: 0; }
  .key { font-size: 12px; overflow-wrap: anywhere; }
  .value { overflow-wrap: anywhere; min-width: 0; }
  /* One line per entry; the whole value is on hover. A pending-events cookie
     wrapped to seven lines and pushed its neighbours apart. */
  .value.clip { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .flags { display: inline-flex; gap: 4px; justify-content: flex-end; }
  .pill.shared { color: var(--accent); border-color: currentColor; }
  .fate-list { padding: 8px 10px; border-radius: 8px; background: color-mix(in srgb, var(--ink) 5%, transparent); }
  .fate-list .pk { width: 48px; }
</style>
