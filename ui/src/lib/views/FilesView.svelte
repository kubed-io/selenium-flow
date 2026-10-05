<script lang="ts">
  import FileGrid from '../FileGrid.svelte'
  import type { FilesRootData } from '../types'

  let { data, onshow }: { data: FilesRootData; onshow?: (uri: string) => void } = $props()
  const label = (n: string) => n.charAt(0).toUpperCase() + n.slice(1)
  const UNAVAILABLE = "Open isn't available in this client"
</script>

<section class="section">
  <div class="head"><strong>Files</strong><span class="pill">{data.count}</span></div>
  <div class="body"><FileGrid files={data.files} layout="row" empty="Nothing here yet." /></div>
</section>
<div class="chips">
  {#each data.folders as f (f.uri)}
    {#if onshow}
      <button type="button" class="chip" onclick={() => onshow(f.uri)}>
        <span>{label(f.name)}</span><span class="pill">{f.count}</span>
      </button>
    {:else}
      <span class="chip" title={UNAVAILABLE}><span>{label(f.name)}</span><span class="pill">{f.count}</span></span>
    {/if}
  {/each}
</div>

<style>
  .chips { display: flex; flex-wrap: wrap; gap: var(--gap); margin-top: var(--gap); }
  .chip {
    display: inline-flex; align-items: center; gap: 8px; min-height: 44px; padding: 0 14px;
    border: 1px solid var(--line); border-radius: 999px; background: var(--panel);
    color: var(--ink); font: inherit;
  }
  button.chip { cursor: pointer; }
  button.chip:hover { border-color: var(--accent); }
  button.chip:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
</style>
