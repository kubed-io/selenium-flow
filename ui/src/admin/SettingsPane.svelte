<script lang="ts">
  import { onMount } from 'svelte'
  import type { SettingRow, SettingsPayload } from '../lib/types'
  import type { Api } from './api'
  import { Latest } from './latest'

  let { api }: { api: Api } = $props()
  // Raw: an API response, replaced wholesale, never mutated.
  let data = $state.raw<SettingsPayload | null>(null)
  let error = $state<string | null>(null)
  const loads = new Latest()
  const WIKI = 'https://github.com/kubed-io/selenium-flow/wiki/Configuration'
  // Weakest first, which is also precedence: the order is the legend.
  const SOURCES = ['default', 'file', 'env', 'args'] as const

  onMount(() => {
    void loads.run((signal) => api<SettingsPayload>('/admin/settings', 'GET', undefined, signal),
      (d) => { data = d; error = null }, (e) => { error = e.message })
    return () => loads.abort()
  })

  /* Show, never explain: dots say sensitive, a dash says unset. */
  function shown(row: SettingRow): string {
    if (row.sensitive) return row.set ? '●●●●' : ''
    if (Array.isArray(row.value)) return row.value.length ? row.value.join(', ') : '—'
    if (row.value === null || row.value === undefined || row.value === '') return '—'
    return String(row.value)
  }
</script>

<section id="paneSettings">
  {#if error}
    <div class="empty error">{error}</div>
  {:else if !data}
    <div class="empty">Loading…</div>
  {:else}
    <h2>Settings</h2>
    <p class="small muted">How this server was started. Read-only. <a href={WIKI} target="_blank" rel="noopener noreferrer">Every setting is described on the wiki →</a></p>
    <div class="legend">{#each SOURCES as s (s)}<span class={['pill', 'src', s]}>{s}</span>{/each}</div>
    {#each data.sections as section (section.name)}
      <div class="card settings-card">
        <strong>{section.name}</strong>
        <div class="small muted">{section.description}</div>
        {#each section.settings as row (row.key)}
          <div class="setting" data-key={row.key}>
            <span class="info" role="img" aria-label={row.description} data-tip={row.description}>i</span>
            <code class="key">{row.name}</code>
            <span class="value">{shown(row)}</span>
            <code class="file">{row.source === 'file' ? (row.file ?? '') : ''}</code>
            <span class={['pill', 'src', row.source]}>{row.source}</span>
          </div>
        {/each}
      </div>
    {/each}
  {/if}
</section>

<style>
  .legend { display: flex; justify-content: flex-end; gap: 4px; margin: 8px 0; }
  .settings-card { display: flex; flex-direction: column; gap: 2px; margin-bottom: var(--gap); }
  .setting { display: grid; grid-template-columns: 16px 240px 1fr auto auto; align-items: center; gap: 8px; padding: 6px 0; border-top: 1px solid var(--line); }
  .key, .file { font-size: 12px; }
  .file { color: var(--muted); }
  .value { overflow-wrap: anywhere; }
  .info { position: relative; display: inline-grid; place-items: center; width: 14px; height: 14px; border: 1px solid var(--muted); border-radius: 50%; color: var(--muted); font-size: 10px; font-weight: 600; cursor: help; }
  .info:hover::after {
    content: attr(data-tip); position: absolute; left: 20px; top: -4px; z-index: 5; white-space: nowrap;
    padding: 4px 8px; border-radius: 6px; background: var(--ink); color: var(--panel); font-size: 12px; font-weight: 400;
  }
  .pill.src.default { color: var(--muted); }
  .pill.src.file { color: var(--accent); border-color: currentColor; }
  .pill.src.env { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
  .pill.src.arg, .pill.src.args { background: var(--ink); border-color: var(--ink); color: var(--panel); }
</style>
