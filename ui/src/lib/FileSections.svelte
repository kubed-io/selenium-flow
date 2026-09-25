<script lang="ts">
  import FileGrid from './FileGrid.svelte'
  import type { FileEntry } from './types'

  let { data, base = '' }: {
    data: { downloads?: FileEntry[]; screenshots?: FileEntry[]; files?: FileEntry[] }
    base?: string
  } = $props()

  // Only the MCP App renders this component, on a sandbox origin of the
  // host's choosing — a server-relative `url` would resolve against the
  // wrong server. Swap in `absolute_url` where the server signed one, and
  // leave `base` at '' below (Copilot review, PR #42).
  const absolute = (files: FileEntry[]) => files.map((f) => ({ ...f, url: f.absolute_url ?? f.url }))

  // Downloads, Screenshots, Files — each its own grid, so paging stays in a row.
  const rows = $derived([
    ['Downloads', absolute(data.downloads ?? []), 'No downloads.'],
    ['Screenshots', absolute(data.screenshots ?? []), 'No screenshots yet.'],
    ['Files', absolute(data.files ?? []), 'Nothing here yet — prints land here, and anything you keep.'],
  ] as const)
</script>

{#each rows as [title, files, empty] (title)}
  <section class="row">
    <div class="head"><strong>{title}</strong><span class="pill">{files.length}</span></div>
    <div class="body"><FileGrid {files} {base} {empty} /></div>
  </section>
{/each}
