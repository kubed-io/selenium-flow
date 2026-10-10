<script lang="ts">
  import { hostPath, isFailed, statusText } from '../capture'
  import type { CaptureOff, NetworkData, RequestRow } from '../types'

  let { data, uri, onshow }: { data: NetworkData | CaptureOff; uri?: string; onshow?: (uri: string) => void } = $props()
  const UNAVAILABLE = "Open isn't available in this client"

  const requests = $derived('requests' in data ? data.requests : [])
  const failed = $derived(requests.filter(isFailed).length)
  const xhr = $derived(requests.filter((r) => ['xhr', 'fetch'].includes(r.type.toLowerCase())).length)
  const statics = $derived('not_kept' in data ? data.not_kept.static : 0)
</script>

<div class="card">
  <div class="row head">
    <strong>Network</strong>
    <code class="uri small muted grow">{uri ?? 'workspace://network'}</code>
  </div>
  {#if !('requests' in data)}
    <p class="small muted">Capture is off for this workspace. Open the browser with open_session(capture=true).</p>
  {:else}
    <div class="chips">
      <span class="chip"><span>Failed</span><span class="pill">{failed}</span></span>
      <span class="chip"><span>XHR · fetch</span><span class="pill">{xhr}</span></span>
    </div>
    <ul class="reqs">
      {#each requests as r (r.id)}
        <li>
          {#if onshow}
            <button type="button" class="req" onclick={() => onshow('workspace://network/' + r.id)}>
              {@render line(r)}
            </button>
          {:else}
            <div class="req" title={UNAVAILABLE}>{@render line(r)}</div>
          {/if}
        </li>
      {/each}
    </ul>
    {#if statics > 0}
      <p class="small muted">{statics} successful static requests not shown · since the last navigation</p>
    {/if}
  {/if}
</div>

{#snippet line(r: RequestRow)}
  <span class="method small">{r.method}</span>
  <span class="status small" class:bad={isFailed(r)}>{statusText(r)}</span>
  <span class="url grow">{hostPath(r.url)}</span>
  {#if r.took_ms !== null}<span class="small muted">{r.took_ms} ms</span>{/if}
{/snippet}

<style>
  .head { margin-bottom: 10px; }
  .reqs { list-style: none; margin: 10px 0 0; padding: 0; border: 1px solid var(--line); border-radius: var(--radius); }
  .reqs li + li { border-top: 1px solid var(--line); }
  .req {
    display: flex; align-items: baseline; gap: var(--gap); width: 100%; min-height: 40px; padding: 8px 12px;
    border: 0; border-radius: 0; background: transparent; color: var(--ink); font: inherit; text-align: left;
  }
  button.req { cursor: pointer; }
  button.req:hover { background: color-mix(in srgb, var(--ink) 5%, transparent); }
  button.req:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .method { flex: none; min-width: 3.5em; font-weight: 600; }
  .status { flex: none; min-width: 3em; }
  .status.bad { color: var(--bad, #d33); }
  .url { font-family: var(--mono, ui-monospace, monospace); font-size: 12px; overflow-wrap: anywhere; min-width: 0; }
</style>
