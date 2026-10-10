<script lang="ts">
  import FileGrid from '../FileGrid.svelte'
  import type { FolderData } from '../types'

  let { data, onshow }: { data: FolderData; onshow?: (uri: string) => void } = $props()
  const title = $derived(data.folder.charAt(0).toUpperCase() + data.folder.slice(1))
  // With the host's tools a tile opens its own view; without, the lightbox.
  const drill = $derived(onshow ? (i: number) => { const u = data.files[i]?.uri; if (u) onshow(u) } : undefined)
</script>

<section class="section">
  <div class="head"><strong>{title}</strong><span class="pill">{data.count}</span></div>
  <div class="body"><FileGrid files={data.files} layout="row" empty="Nothing here yet." onopen={drill} /></div>
</section>
