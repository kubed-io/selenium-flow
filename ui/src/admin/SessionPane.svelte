<script lang="ts">
  import { captureFact, idle } from '../lib/format'
  import type { SentCapability, WorkspaceRow } from '../lib/types'
  import Section from './Section.svelte'

  /* The Session tab (spec 2026-10-26-session-capabilities, design/session.md):
     what the workspace asks for, what the Grid gave, and every capability the
     last open sent with where it came from. Read-only, like Settings. */
  let { row, hidden }: { row: WorkspaceRow; hidden: boolean } = $props()

  const VENDOR = new Set(['goog:chromeOptions', 'moz:firefoxOptions'])

  const browser = $derived(row.browser || 'chrome')
  const asked = $derived(row.asked_version && row.asked_version !== 'latest' ? row.asked_version : 'latest')
  const sent = $derived(row.sent ?? [])

  /* What a next explicit open_session() carries over, and while a browser is
     open, what it is doing. RECORD and CAPTURE are not carried (ruling 11). */
  const workspace = $derived(([
    ['key', row.name ? null : row.key],
    ['held by', row.name ? null : row.owner],
    ['browser', `${browser} · ${asked}`],
    ['window', row.window],
    ['record', row.live && row.recording ? 'on' : null],
    ['capture', row.live ? captureFact(row) : null],
  ] as [string, unknown][]).filter(([, v]) => v))

  /* How the last session ended. Only an End this server saw has a time;
     a browser the Grid no longer lists was reaped, and nothing watched it. */
  function ended(): string | null {
    if (row.ended) {
      const when = new Date(row.ended.at * 1000).toLocaleString()
      return `${when} · ended by the ${row.ended.by === 'admin' ? 'admin' : 'caller'}`
    }
    if (!row.attached) return null
    return row.grid_timeout ? `reaped after ${row.grid_timeout} s idle` : 'gone from the Grid'
  }

  const liveView = $derived(row.ran?.live_view == null ? null : row.ran.live_view ? 'yes' : 'no')

  /* What the Grid gave this browser, or the last one. Not drawn at all until
     a browser has been opened here. */
  const opened = $derived(Boolean(row.started || row.ran || row.session_id))
  const grid = $derived(([
    ['browser', `${browser} ${row.ran?.version ?? row.version ?? ''}`.trim()],
    ['id', row.session_id],
    ['node', row.ran?.node ?? row.node],
    ['started', row.started ? new Date(row.started * 1000).toLocaleString() : null],
    ['idle timeout', idle(row.grid_timeout, row.live)],
    row.live ? ['live view', liveView] : ['ended', ended()],
  ] as [string, unknown][]).filter(([, v]) => v))

  /* A vendor block reads as what it holds, never its values: `args 1 · prefs 6`. */
  function shown(entry: SentCapability): string {
    const v = entry.value
    if (VENDOR.has(entry.key) && v && typeof v === 'object' && !Array.isArray(v)) {
      const block = v as Record<string, unknown>
      return Object.keys(block).map((k) => {
        const inner = block[k]
        if (k === 'args' && Array.isArray(inner)) return `args ${inner.length}`
        if (k === 'prefs' && inner && typeof inner === 'object') return `prefs ${Object.keys(inner).length}`
        return k
      }).join(' · ')
    }
    if (v !== null && typeof v === 'object') return JSON.stringify(v)
    return String(v)
  }
</script>

<div id="paneSession" {hidden}>
  <div class="groups">
    <div class="group" id="workspaceCard">
      <div class="label">workspace</div>
      {#each workspace as [k, v] (k)}
        <div class="fact"><div class="k">{k}</div><div class="v">{v}</div></div>
      {/each}
    </div>
    {#if opened}
      <div class="group" id="gridCard">
        <div class="label">grid session</div>
        {#each grid as [k, v] (k)}
          <div class="fact"><div class="k">{k}</div><div class="v" class:mono={k === 'id' || k === 'node'}>{v}</div></div>
        {/each}
      </div>
    {/if}
  </div>

  <Section id="capabilitiesSection" title="Capabilities" count={sent.length} collapsed>
    {#each sent as entry (entry.key)}
      <div class="cap mono">
        <span class="ck">{entry.key}</span>
        <span class="cv">{shown(entry)}</span>
        <span class="pills">{#each entry.sources as s (s)}<span class={['pill', 'src', s]}>{s}</span>{/each}</span>
      </div>
    {:else}
      <p class="small muted">Nothing sent yet: no browser has been opened in this workspace since this server kept it.</p>
    {/each}
  </Section>
</div>

<style>
  #paneSession .groups { margin-bottom: var(--gap); }
  #paneSession .fact + .fact { margin-top: 8px; }
  .cap { display: grid; grid-template-columns: 220px 1fr auto; gap: 8px; align-items: center; padding: 6px 12px; border-top: 1px solid var(--line); font-size: 12px; }
  .ck { color: var(--muted); word-break: break-all; }
  .cv { word-break: break-all; }
  .pills { display: flex; gap: 4px; justify-content: flex-end; }
</style>
