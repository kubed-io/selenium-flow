<script lang="ts">
  import FileGrid from '../FileGrid.svelte'
  import type { FolderData } from '../types'

  let { data }: { data: FolderData; onshow?: (uri: string) => void } = $props()
  const title = $derived(data.folder.charAt(0).toUpperCase() + data.folder.slice(1))
</script>

<section class="row">
  <div class="head"><strong>{title}</strong><span class="pill">{data.count}</span></div>
  <div class="body"><FileGrid files={data.files} empty="Nothing here yet." /></div>
</section>
