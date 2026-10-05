<script lang="ts">
  import { flip } from 'svelte/animate'
  import { fade } from 'svelte/transition'
  import { ms } from '../motion'
  import FileTile from './FileTile.svelte'
  import Lightbox from './Lightbox.svelte'
  import type { FileEntry } from './types'

  let { files, base = '', action, empty = 'No files in this session yet.', layout = 'grid', onopen, onkeep, ondelete }: {
    files: FileEntry[]
    base?: string
    /** `row`: one horizontal strip, for an inline app view that must not grow tall. */
    layout?: 'grid' | 'row'
    action?: 'keep' | 'delete'
    empty?: string
    onopen?: (i: number) => void
    onkeep?: (f: FileEntry) => Promise<boolean>
    ondelete?: (f: FileEntry) => void
  } = $props()

  // The viewer a surface with no page around it (the MCP App) opens by itself.
  let open = $state<number | null>(null)
</script>

{#if !files.length}
  <div class="empty">{empty}</div>
{:else}
  <div class="files" class:strip={layout === 'row'}>
    {#each files as f, i (f.name)}
      <div class="file" animate:flip={{ duration: ms(150) }} out:fade|local={{ duration: ms(120) }}>
        <FileTile {f} {base} {action} {onkeep} {ondelete}
                  onopen={() => (onopen ? onopen(i) : (open = i))} />
      </div>
    {/each}
  </div>
{/if}
{#if open !== null}
  <Lightbox {files} index={open} {base} onclose={() => (open = null)} />
{/if}
