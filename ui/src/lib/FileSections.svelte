<script lang="ts">
  import FileGrid from './FileGrid.svelte'
  import type { FileEntry } from './types'

  let { data, base = '' }: {
    data: { downloads?: FileEntry[]; screenshots?: FileEntry[]; files?: FileEntry[] }
    base?: string
  } = $props()

  // Only the MCP App renders this component, on a sandbox origin of the
  // host's choosing, so `base` stays '': the server makes `url` absolute when
  // it knows its public address (Copilot review, PR #42).

  // Downloads, Screenshots, Files — each its own grid, so paging stays in a row.
  const rows = $derived([
    ['Downloads', data.downloads ?? [], 'No downloads.'],
    ['Screenshots', data.screenshots ?? [], 'No screenshots yet.'],
    ['Files', data.files ?? [], 'Nothing here yet — prints land here, and anything you keep.'],
  ] as const)
</script>

{#each rows as [title, files, empty] (title)}
  <section class="row">
    <div class="head"><strong>{title}</strong><span class="pill">{files.length}</span></div>
    <div class="body"><FileGrid {files} {base} {empty} /></div>
  </section>
{/each}
