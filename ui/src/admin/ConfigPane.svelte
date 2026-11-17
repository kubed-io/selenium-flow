<script lang="ts">
  import { span } from '../lib/format'
  import type { WorkspaceModel } from './workspace.svelte'

  let { m, hidden, onedit, onremove }: { m: WorkspaceModel; hidden: boolean; onedit: () => void; onremove: () => void } = $props()

  // Weakest first, as on Settings: the order is the legend.
  const SOURCES = ['default', 'workspace'] as const
  const SECTIONS: Record<string, string> = { flows: 'Which global flows this workspace offers.' }
  const ABOUT: Record<string, string> = {
    'flows.global': 'Rows of tags joined by commas. A global flow that carries every tag of any row is offered.',
  }

  const sections = $derived.by(() => {
    const rows = Object.entries(m.config?.config ?? {})
    const named = [...new Set(rows.map(([path]) => path.split('.')[0]))]
    return named.map((name) => ({ name, rows: rows.filter(([path]) => path.split('.')[0] === name) }))
  })

  /* A filter's rows, each the tags it joins. */
  const filter = (value: unknown): string[][] | null =>
    Array.isArray(value) ? value.map((row) => String(row).split(',').map((t) => t.trim()).filter(Boolean)) : null
</script>

<section id="paneConfig" {hidden}>
  {#if m.configError}
    <div class="empty error">{m.configError}</div>
  {:else if !m.config}
    <div class="empty">Loading…</div>
  {:else}
    <div class="card config-card">
      <div class="row">
        <span class="grow small muted">{m.config.kept ? 'This workspace has a config, so it never expires.' : m.config.ttl ? `No config: this workspace expires ${span(m.config.ttl)} after its last use.` : 'No config: this workspace expires when unused.'}</span>
        <span class="legend">{#each SOURCES as s, i (s)}{#if i}<span class="small muted">&lt;</span>{/if}<span class={['pill', 'src', s]}>{s}</span>{/each}</span>
        <button type="button" onclick={onedit}>Edit YAML</button>
        {#if m.config.kept}<button type="button" class="danger" onclick={onremove}>Remove config</button>{/if}
      </div>
      {#each sections as section (section.name)}
        <strong class="section-name">{section.name}</strong>
        <div class="small muted">{SECTIONS[section.name] ?? ''}</div>
        {#each section.rows as [path, row] (path)}
          {@const rows = filter(row.value)}
          <div class="setting" data-key={path}>
            <span class="info" role="button" tabindex="0" aria-label={ABOUT[path] ?? path} data-tip={ABOUT[path] ?? path}>i</span>
            <code class="key">{path}</code>
            <span class="value">
              {#if path === 'flows.global' && row.value === null}
                every global flow
              {:else if path === 'flows.global' && rows?.length === 0}
                no global flow
              {:else if rows}
                {#each rows as tags, i (i)}
                  {#if i}<span class="small muted or">or</span>{/if}
                  <span class="filter-row">{#each tags as tag, j (j)}<span class="tag">{tag}</span>{/each}</span>
                {/each}
              {:else}
                {row.value === null ? '—' : String(row.value)}
              {/if}
            </span>
            <span class={['pill', 'src', row.source]}>{row.source}</span>
          </div>
        {/each}
      {/each}
    </div>
  {/if}
</section>

<style>
  .config-card { display: flex; flex-direction: column; gap: 2px; }
  .legend { display: inline-flex; align-items: center; gap: 4px; }
  .section-name { margin-top: 12px; }
  .setting { display: grid; grid-template-columns: 16px 240px 1fr auto; align-items: start; gap: 8px; padding: 6px 0; border-top: 1px solid var(--line); }
  .key { font-size: 12px; }
  .value { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; overflow-wrap: anywhere; }
  .filter-row { display: flex; flex-wrap: wrap; gap: 4px; }
  .tag { padding: 1px 8px; border-radius: 6px; background: var(--line); font-size: 12px; }
  .pill.src.workspace { color: var(--ok); border-color: currentColor; }
  .info { position: relative; display: inline-grid; place-items: center; width: 14px; height: 14px; border: 1px solid var(--muted); border-radius: 50%; color: var(--muted); font-size: 10px; font-weight: 600; cursor: help; }
  .info:hover::after, .info:focus::after {
    content: attr(data-tip); position: absolute; left: 20px; top: -4px; z-index: 5; white-space: nowrap;
    padding: 4px 8px; border-radius: 6px; background: var(--ink); color: var(--panel); font-size: 12px; font-weight: 400;
  }
</style>
