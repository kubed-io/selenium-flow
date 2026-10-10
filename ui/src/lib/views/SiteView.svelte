<script lang="ts">
  import { until } from '../format'
  import type { SiteCookie, SiteDetail } from '../types'

  let { data }: { data: SiteDetail; onshow?: (uri: string) => void } = $props()

  // httpOnly · secure · the domain when a parent set it · how long it lasts.
  const facts = (c: SiteCookie) =>
    [c.http_only ? 'httpOnly' : '', c.secure ? 'secure' : '', c.shared ? (c.domain ?? '') : '', c.expiry ? until(c.expiry) : 'session']
      .filter(Boolean).join(' · ')
  // Per origin, never merged; an empty storage is not drawn.
  const groups = $derived(
    data.storage
      .flatMap((e) => [
        { label: 'Local storage', origin: e.origin, entries: Object.entries(e.local_storage) },
        { label: 'Session storage', origin: e.origin, entries: Object.entries(e.session_storage) },
      ])
      .filter((g) => g.entries.length),
  )
</script>

<div class="card">
  <div class="row head">
    <strong>{data.site}</strong>
    <code class="uri small muted grow">{data.uri ?? ''}</code>
  </div>
  {#if !data.cookies.length && !groups.length}
    <p class="small muted">Nothing saved for this site.</p>
  {/if}
  {#if data.cookies.length}
    <div class="block">
      <h3>Cookies</h3>
      {#each data.cookies as c (c.name + '|' + c.domain + '|' + c.path)}
        <div class="line">
          <code class="key">{c.name}</code>
          <span class="value clip" title={c.value}>{c.value}</span>
          <span class="small muted">{facts(c)}</span>
        </div>
      {/each}
    </div>
  {/if}
  {#each groups as g (g.label + g.origin)}
    <div class="block">
      <h3>{g.label} · {g.origin}</h3>
      {#each g.entries as [k, v] (k)}
        <div class="line"><code class="key">{k}</code><span class="value clip" title={v}>{v}</span></div>
      {/each}
    </div>
  {/each}
</div>

<style>
  .head { margin-bottom: 10px; flex-wrap: wrap; }
  .block { margin-top: 10px; padding: 8px 12px; border-radius: var(--radius); background: color-mix(in srgb, var(--ink) 4%, transparent); }
  h3 { margin: 0 0 4px; font-size: 11px; font-weight: 400; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }
  .line { display: grid; grid-template-columns: minmax(90px, 160px) minmax(0, 1fr) auto; align-items: baseline; gap: 8px; padding: 3px 0; }
  .key { font-size: 12px; overflow-wrap: anywhere; }
  .value.clip { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-family: var(--mono, ui-monospace, monospace); }
</style>
