<script lang="ts">
  import FileGrid from '../FileGrid.svelte'
  import type { FilesRootData } from '../types'

  let { data, onshow }: { data: FilesRootData; onshow?: (uri: string) => void } = $props()
  const label = (n: string) => n.charAt(0).toUpperCase() + n.slice(1)
  const UNAVAILABLE = "Open isn't available in this client"
  // With the host's tools a tile opens its own view; without, the lightbox.
  const drill = $derived(onshow ? (i: number) => { const u = data.files[i]?.uri; if (u) onshow(u) } : undefined)
</script>

<section class="section">
  <div class="head"><strong>Files</strong><span class="pill">{data.count}</span></div>
  <div class="body"><FileGrid files={data.files} layout="row" empty="Nothing here yet." onopen={drill} /></div>
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
