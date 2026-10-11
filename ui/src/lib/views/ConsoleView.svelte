<script lang="ts">
  import { clock, isError } from '../capture'
  import type { CaptureOff, ConsoleData } from '../types'

  const SHOWN = 4
  let { data, uri }: { data: ConsoleData | CaptureOff; uri?: string } = $props()

  const entries = $derived('entries' in data ? [...data.entries].sort((a, b) => b.seq - a.seq) : [])
  /* The listing's own tally: show sends only the newest rows, so counting
     them would count only those. */
  const tally = $derived('tally' in data ? data.tally : undefined)
  const errors = $derived(tally?.errors ?? entries.filter(isError).length)
  const warnings = $derived(tally?.warnings ?? entries.filter((e) => e.level === 'warn' || e.level === 'warning').length)
  const rows = $derived(entries.slice(0, SHOWN))
  /* show sends at most the newest 200; `count` is how many there are. */
  const cut = $derived('count' in data && data.count > entries.length ? data.count : 0)
</script>

<div class="card">
  <div class="row head">
    <strong>Console</strong>
    <code class="uri small muted grow">{uri ?? 'workspace://console'}</code>
  </div>
  {#if !('entries' in data)}
    <p class="small muted">Capture is off for this workspace. Open the browser with open_session(capture=true).</p>
  {:else}
    <div class="chips">
      <span class="chip"><span>Errors</span><span class="pill">{errors}</span></span>
      <span class="chip"><span>Warnings</span><span class="pill">{warnings}</span></span>
    </div>
    <ul class="entries">
      {#each rows as e (e.seq)}
        <li class="entry">
          <span class="pill level" class:warn={e.level === 'warn' || e.level === 'warning'} class:err={isError(e)}>{e.level}</span>
          <span class="text grow">{e.text}</span>
          <span class="small muted">{clock(e.at)}</span>
        </li>
      {/each}
    </ul>
    {#if entries.length > SHOWN}
      <p class="small muted">+{entries.length - SHOWN} more · since the last navigation</p>
    {/if}
    {#if cut}
      <p class="small muted">showing the newest {entries.length} of {cut.toLocaleString('en-US')}</p>
    {/if}
  {/if}
</div>

<style>
  .head { margin-bottom: 10px; }
  .entries { list-style: none; margin: 10px 0 0; padding: 0; }
  .entry { display: flex; align-items: baseline; gap: var(--gap); padding: 6px 0; border-top: 1px solid var(--line); }
  .text { font-family: var(--mono, ui-monospace, monospace); font-size: 12px; overflow-wrap: anywhere; min-width: 0; }
  .level { flex: none; }
  .level.err { color: var(--bad, #d33); border-color: currentColor; }
  .level.warn { color: var(--warn); border-color: currentColor; }
</style>
