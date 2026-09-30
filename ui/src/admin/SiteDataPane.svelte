<script lang="ts">
  import { untrack } from 'svelte'
  import { ago } from '../lib/format'
  import type { SiteCookie, SiteDetail, SiteRow } from '../lib/types'
  import { sessionPath, type Api } from './api'
  import { folds } from './folds.svelte'
  import type { ModalSpec } from './modal'
  import Section from './Section.svelte'
  import type { SessionModel } from './session.svelte'

  let { m, api, ask, refuseIfGone, isGone, hidden }: {
    m: SessionModel
    api: Api
    ask: <T>(spec: ModalSpec<T>) => void
    refuseIfGone: () => void
    isGone: () => boolean
    hidden: boolean
  } = $props()

  const data = $derived(m.siteData)
  // Ends in `Section`: Section derives its body's id by replacing that.
  const idOf = (r: SiteRow) => 'site-' + m.key + ':' + r.site + 'Section'

  // The first saved row opens, the rest stay shut — once; after that the fold
  // is the reader's.
  $effect.pre(() => {
    const sites = data?.sites ?? []
    // Not row 0: a secret-only row can sort first and has nothing to open.
    const first = sites.find((r) => r.saved)
    untrack(() => sites.forEach((r) => { folds[idOf(r)] ??= r === first }))
  })

  const plural = (n: number, one: string, many = one + 's') => n + ' ' + (n === 1 ? one : many)
  const counts = (r: SiteRow) => [
    r.saved ? plural(r.cookies, 'cookie') : 'nothing saved',
    ...(r.saved ? [r.local_storage + ' local', r.session_storage + ' session'] : []),
    plural(r.secrets.length, 'secret'),
  ].join(' · ')

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
        origin: r.origin ?? r.site,
        goes: [
          ...(d?.own_cookies ?? []),
          ...Object.keys(d?.local_storage ?? {}),
          ...Object.keys(d?.session_storage ?? {}),
        ],
        stays: [
          ...(d?.kept_shared ?? []).map((c) => ({
            key: [c.name, c.domain, c.path].join('|'),
            label: c.name + ', shared with ' + c.domain + (c.path && c.path !== '/' ? ' ' + c.path : ''),
          })),
          ...r.secrets.map((s) => ({ key: 'secret|' + s.name, label: s.name + ' secret' })),
        ],
      },
      confirm: 'Forget', danger: true,
      onconfirm: async () => {
        refuseIfGone()
        await api(sessionPath(m.key, '/site-data/' + encodeURIComponent(r.site)), 'DELETE')
        if (!isGone()) void m.loadSiteData()
      },
    })
  }
</script>

{#snippet forgetBody(f: Forget)}
  <p>{f.origin} — the next browser comes back signed out here; one open now keeps what it has.</p>
  <div class="pairs fate-list">
    {#if f.goes.length}<div class="pair"><span class="pk">goes</span><span class="pv">{f.goes.join(' · ')}</span></div>{/if}
    {#each f.stays as s (s.key)}<div class="pair"><span class="pk">stays</span><span class="pv">{s.label}</span></div>{/each}
  </div>
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
  {:else}
    {#if data.saved_sites === 0}
      <p class="small muted">Nothing saved — an agent calls <code>save_site_data</code> after signing in.</p>
    {/if}
    {#each data.sites as r (r.site)}
      {@const d = data.details[r.site]}
      <Section id={idOf(r)} title={r.origin ?? r.site}>
        {#snippet summary()}
          {#if r.saved_at}<span class="pill saved">saved {ago(r.saved_at * 1000)}</span>{/if}
          <span class="small muted">{counts(r)}</span>
        {/snippet}
        {#snippet actions()}
          {#if r.saved}<button class="danger" onclick={() => forget(r)}>Forget</button>{/if}
        {/snippet}
        {#if !r.saved}
          <p class="small muted">Nothing saved. It stays listed because a secret is allowed here.</p>
        {:else if d}
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
          <h3>Local storage</h3>
          {@render kv(Object.entries(d.local_storage))}
          <h3>Session storage</h3>
          {@render kv(Object.entries(d.session_storage))}
        {/if}
        {#if r.secrets.length}
          <h3>Secrets</h3>
          {#each r.secrets as s (s.name)}
            <div class="line secret">
              <span class="key"><span>🔑</span> <code>{s.name}</code></span>
              <span class="value">{s.description ?? ''}</span>
              <span class="flags">{#each s.keys as k (k)}<span class="pill">{k}</span>{/each}</span>
            </div>
          {/each}
        {/if}
      </Section>
    {/each}
  {/if}
</div>

<style>
  h3 { margin: 14px 0 4px; font-size: 11px; font-weight: 400; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }
  .line { display: grid; grid-template-columns: 240px 1fr auto; align-items: center; gap: 8px; padding: 6px 0; border-top: 1px solid var(--line); }
  .line.cookie { grid-template-columns: 240px 1fr auto auto; }
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
