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
    {#if data.settings?.record && data.live}<span class="pill rec">● REC</span>{/if}
    {#if data.capture && data.capturing}<span class="pill capture">● capture</span>{/if}
    {#if data.capture && !data.capturing}<span class="pill capture-paused">capture paused</span>{/if}
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
  </div>
  {#if data.site_data || data.capture}
    <div class="chips">
      {#if data.capture}
        {#each [
          /* With errors it opens on them: they are why anyone clicks, and a
             chatty page's whole console is more than show draws. */
          { name: 'Console', uri: (data.captured?.errors ?? 0) > 0 ? 'workspace://console?level=error' : 'workspace://console', text: `${data.captured?.errors ?? 0} errors` },
          { name: 'Network', uri: 'workspace://network', text: `${data.captured?.failed_requests ?? 0} failed` },
        ] as c (c.uri)}
          {#if onshow}
            <button type="button" class="chip" onclick={() => onshow(c.uri)}><span>{c.name}</span><span class="pill">{c.text}</span></button>
          {:else}
            <span class="chip" title="Open isn't available in this client"><span>{c.name}</span><span class="pill">{c.text}</span></span>
          {/if}
        {/each}
      {/if}
      {#if data.site_data}
      {#if onshow && data.site_data.uri}
        <button type="button" class="chip" onclick={() => onshow(data.site_data!.uri!)}>
          <span>Site data</span><span class="pill">{data.site_data.sites}</span>
        </button>
      {:else}
        <span class="chip" title="Open isn't available in this client"><span>Site data</span><span class="pill">{data.site_data.sites}</span></span>
      {/if}
      {/if}
    </div>
  {/if}
</div>
