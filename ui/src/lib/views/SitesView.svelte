<script lang="ts">
  import { clock } from '../format'
  import { siteCounts } from '../sites'
  import type { SitesData } from '../types'

  let { data, onshow }: { data: SitesData; onshow?: (uri: string) => void } = $props()
  const UNAVAILABLE = "Open isn't available in this client"
</script>

<div class="card">
  <div class="row head">
    <strong>Site data</strong>
    <code class="uri small muted grow">{data.uri ?? 'workspace://site-data'}</code>
    {#if data.saved_at}<span class="small muted">saved {clock(data.saved_at * 1000)}</span>{/if}
  </div>
  {#if !data.sites.length}
    <p class="small muted">Nothing saved — an agent calls <code>save_site_data</code> after signing in.</p>
  {:else}
    <ul class="sitelist">
      {#each data.sites as r (r.site)}
        <li>
          {#if onshow && r.uri}
            <button type="button" class="siterow" onclick={() => onshow(r.uri!)}>
              <strong class="grow">{r.site}</strong><span class="pill">{siteCounts(r)}</span><span aria-hidden="true">›</span>
            </button>
          {:else}
            <div class="siterow" title={UNAVAILABLE}><strong class="grow">{r.site}</strong><span class="pill">{siteCounts(r)}</span></div>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .head { margin-bottom: 10px; }
  .sitelist { list-style: none; margin: 0; padding: 0; border: 1px solid var(--line); border-radius: var(--radius); }
  .sitelist li + li { border-top: 1px solid var(--line); }
  .siterow {
    display: flex; align-items: center; gap: var(--gap); width: 100%; min-height: 44px; padding: 0 12px;
    border: 0; border-radius: 0; background: transparent; color: var(--ink); font: inherit; text-align: left;
  }
  button.siterow { cursor: pointer; }
  button.siterow:hover { background: color-mix(in srgb, var(--ink) 5%, transparent); }
  button.siterow:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
</style>
