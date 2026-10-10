<script lang="ts">
  import { kb, pathOf, statusText } from '../capture'
  import type { BodyResult, Header, RequestDetail } from '../types'

  let { data, uri, onshow, oncall }: {
    data: RequestDetail
    uri?: string
    onshow?: (uri: string) => void
    oncall?: (name: string, args: Record<string, unknown>) => Promise<unknown>
  } = $props()

  let body = $state.raw<BodyResult | null>(null)
  let problem = $state<string | null>(null)
  let busy = $state(false)

  async function fetchBody() {
    busy = true
    problem = null
    try {
      body = (await oncall!('network_body', { id: data.id })) as BodyResult
    } catch (err) {
      problem = err instanceof Error ? err.message : String(err)
    } finally {
      busy = false
    }
  }

  const took = $derived(data.took_ms === null ? '—' : `${data.took_ms} ms`)
</script>

<div class="card">
  {#if onshow}
    <button type="button" class="back" onclick={() => onshow('workspace://network')}>← Network</button>
  {/if}
  <div class="row head">
    <strong class="grow">{data.method} {pathOf(data.url)}</strong>
    <span class="pill">{statusText(data)}</span>
  </div>
  <code class="uri small muted">{uri ?? data.uri ?? ''}</code>

  <div class="block">
    <h3>General</h3>
    <div class="line"><span class="k">url</span><span class="v">{data.url}</span></div>
    <div class="line"><span class="k">type</span><span class="v">{data.type} · {took} · {kb(data.size)}</span></div>
    {#if data.failed || data.error}
      <div class="line"><span class="k">error</span><span class="v">{data.error ?? 'failed'}</span></div>
    {/if}
  </div>
  {@render headers('Request', data.request_headers)}
  {@render headers('Response', data.response_headers)}

  {#if oncall}
    <div class="fetch">
      <button type="button" disabled={busy} onclick={fetchBody}>Fetch body</button>
      <span class="small muted">read from the browser while it lives</span>
    </div>
    {#if problem}<p class="small error">{problem}</p>{/if}
    {#if body}
      {#if body.binary}
        <p class="small muted">{body.mime} · {kb(body.size)} · not shown</p>
      {:else}
        <pre class="body">{body.text ?? ''}</pre>
        {#if body.truncated}<p class="small muted">truncated at 100 kB</p>{/if}
      {/if}
    {/if}
  {/if}
</div>

{#snippet headers(label: string, list: Header[])}
  {#if list?.length}
    <div class="block">
      <h3>{label}</h3>
      {#each list as h, i (i)}
        <div class="line"><span class="k">{h.name}</span><span class="v">{h.value}</span></div>
      {/each}
    </div>
  {/if}
{/snippet}

<style>
  .back { padding: 4px 10px; min-height: 32px; margin-bottom: 8px; }
  .head { margin-bottom: 4px; }
  .block { margin-top: 10px; padding: 8px 12px; border-radius: var(--radius); background: color-mix(in srgb, var(--ink) 4%, transparent); }
  h3 { margin: 0 0 4px; font-size: 11px; font-weight: 400; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }
  .line { display: grid; grid-template-columns: minmax(90px, 160px) minmax(0, 1fr); gap: 8px; padding: 3px 0; }
  .k { font-size: 12px; overflow-wrap: anywhere; }
  .v { font-family: var(--mono, ui-monospace, monospace); font-size: 12px; overflow-wrap: anywhere; }
  .fetch { display: flex; align-items: center; gap: var(--gap); margin-top: 12px; }
  .body { margin: 8px 0 0; padding: 10px 12px; border-radius: var(--radius); background: color-mix(in srgb, var(--ink) 6%, transparent); overflow-x: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; }
</style>
