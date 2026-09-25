<script lang="ts">
  import { browserMark, safeHref } from './format'
  import type { SessionRow } from './types'

  let { data }: { data: SessionRow | null } = $props()
  const s = $derived(data ?? ({ key: '' } as SessionRow))
  const href = $derived(safeHref(s.url))

  // Grouped by how long each fact lives: the session's survive its browser.
  const groups = $derived([
    ['session', [
      ['key', s.name ? null : s.key],
      ['held by', s.name ? null : s.owner],
      ['browser', s.browser],
      ['window', s.window],
      ['started', s.started ? new Date(s.started * 1000).toLocaleString() : null],
    ]],
    ['browser', [['version', s.version], ['id', s.session_id], ['node', s.node]]],
  ].map(([label, facts]) => [label, (facts as [string, unknown][]).filter(([, v]) => v)] as const)
    .filter(([, facts]) => facts.length))
</script>

<div class="card">
  <div class="row" style="margin-bottom:10px">
    <span class="bmark" title={s.browser || 'browser'}>{browserMark(s.browser)}</span>
    <strong class="grow">{s.name || s.owner || 'Session'}</strong>
    {#if s.live}<span class="pill live">live</span>{:else}<span class="pill">idle</span>{/if}
  </div>
  <div class="lastpage">
    <div class="k small muted">last page</div>
    {#if href}
      <a {href} target="_blank" rel="noopener noreferrer">{s.url}</a>
    {:else}
      <span class="small muted">{s.url || 'nowhere yet'}</span>
    {/if}
  </div>
  <div class="groups">
    {#each groups as [label, facts] (label)}
      <div class="group">
        <div class="label">{label}</div>
        {#each facts as [k, v] (k)}
          <div class="fact"><div class="k">{k}</div><div class="v">{v}</div></div>
        {/each}
      </div>
    {/each}
  </div>
</div>
