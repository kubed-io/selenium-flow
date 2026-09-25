<script lang="ts">
  import { ago, bytes, glyphFor } from './format'
  import type { FileEntry } from './types'

  let { f, base, action, onopen, onkeep, ondelete }: {
    f: FileEntry
    base: string
    action?: 'keep' | 'delete'
    onopen: () => void
    onkeep?: (f: FileEntry) => Promise<boolean>
    ondelete?: (f: FileEntry) => void
  } = $props()

  const href = $derived(base + f.url)
  // Disabled for the round trip so a second click cannot keep twice. A reload
  // hands this tile a fresh entry, which re-arms it — as today's redraw did.
  // A writable derived: `keep` sets it, and a new `f` re-evaluates it to false.
  let busy = $derived.by(() => { void f; return false })

  async function keep() {
    busy = true
    if (!(await onkeep?.(f))) busy = false
  }

  function open(e: MouseEvent) {
    // The href stays, so middle-click and "open in new tab" still work.
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
    e.preventDefault()
    onopen()
  }
</script>

{#if action === 'keep'}
  <button type="button" class="act keep" aria-label="Keep {f.name} beyond this browser"
          title="Keep it beyond this browser" disabled={busy} onclick={keep}>📌</button>
{:else if action === 'delete'}
  <button type="button" class="act drop" aria-label="Delete {f.name}" title="Delete this file"
          onclick={() => ondelete?.(f)}>🗑</button>
{/if}
<a class="thumb" {href} target="_blank" rel="noopener" onclick={open}>
  {#if f.image}
    <img loading="lazy" alt={f.name} src={href}>
  {:else}
    <span class="glyph">{glyphFor(f.name)}</span>
  {/if}
</a>
<div class="meta">
  <div class="name">{f.name}</div>
  <div class="small muted">{bytes(f.size)}{f.created ? ' · ' + ago(f.created) : ''}</div>
</div>
