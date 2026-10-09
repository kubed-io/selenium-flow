<script lang="ts">
  import { browserMark, safeHref } from '../format'
  import type { ContextData } from '../types'

  let { data, onshow }: { data: ContextData; onshow?: (uri: string) => void } = $props()
  const href = $derived(safeHref(data.url))
  const who = $derived(data.principal ? (data.principal.username || data.principal.kind) : '')
</script>

<div class="card">
  <div class="row" style="margin-bottom:10px">
    <span class="bmark" title={data.browser || 'browser'}>{browserMark(data.browser)}</span>
    <strong class="grow">{data.workspace}</strong>
    {#if data.live}<span class="pill live">live</span>{:else}<span class="pill">idle</span>{/if}
  </div>
  <div class="lastpage">
    {#if href}
      <a {href} target="_blank" rel="noopener noreferrer">{data.url}</a>
    {:else}
      <span class="small muted">{data.url || 'nowhere yet'}</span>
    {/if}
  </div>
  <div class="row" style="flex-wrap:wrap">
    {#if data.window}<span class="pill">{data.window}</span>{/if}
    {#if who}<span class="pill name">{who}</span>{/if}
    {#if data.site_data}<span class="pill">{data.site_data.sites} {data.site_data.sites === 1 ? 'site' : 'sites'}</span>{/if}
  </div>
  {#if data.site_data}
    <div class="chips">
      {#if onshow && data.site_data.uri}
        <button type="button" class="chip" onclick={() => onshow(data.site_data!.uri!)}>
          <span>Site data</span><span class="pill">{data.site_data.sites}</span>
        </button>
      {:else}
        <span class="chip" title="Open isn't available in this client"><span>Site data</span><span class="pill">{data.site_data.sites}</span></span>
      {/if}
    </div>
  {/if}
</div>
