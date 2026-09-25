<script lang="ts">
  import { untrack } from 'svelte'
  import { fade } from 'svelte/transition'
  import { ms } from '../motion'
  import { glyphFor } from './format'
  import type { FileEntry } from './types'

  interface Action { label: string; run: (f: FileEntry) => Promise<unknown> }
  let { files, index, base = '', action, refresh, onclose }: {
    files: FileEntry[]
    index: number
    base?: string
    action?: Action
    refresh?: (at: number) => Promise<{ files: FileEntry[]; index: number } | null>
    onclose: () => void
  } = $props()

  // Opened once on a list; after an action the caller hands back the next one.
  let list = $state(untrack(() => files.slice()))
  let at = $state(untrack(() => index))
  let busy = $state(false)
  const f = $derived(list[at])
  const href = $derived(base + f.url)

  const step = (d: number) => { const n = at + d; if (n >= 0 && n < list.length) at = n }

  function onkeydown(e: KeyboardEvent) {
    // A confirm opened on top owns the keyboard: without this, Esc closed both.
    if (document.querySelector('.modal')) return
    if (e.key === 'Escape') onclose()
    else if (e.key === 'ArrowLeft') step(-1)
    else if (e.key === 'ArrowRight') step(1)
  }

  async function act() {
    if (!action) return
    busy = true
    try {
      await action.run(list[at])
      const next = refresh ? await refresh(at) : null
      if (!next || !next.files.length) return onclose()
      // The same index: a kept screenshot left the list, so the next one is here.
      list = next.files
      at = Math.min(next.index, list.length - 1)
      busy = false
    } catch (err) {
      busy = false
      if ((err as Error).message !== 'cancelled') alert((err as Error).message)
    }
  }
</script>

<svelte:document {onkeydown} />
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions (Esc is handled on the document) -->
<div class="lightbox" transition:fade|local={{ duration: ms(120) }} onclick={(e) => { if (e.target === e.currentTarget) onclose() }}>
  <div class="head">
    <span class="name">{f.name}</span>
    <span class="pos">{at + 1} / {list.length}</span>
    <span class="grow"></span>
    <button type="button" disabled={at === 0} onclick={() => step(-1)}>‹ Prev</button>
    <button type="button" disabled={at === list.length - 1} onclick={() => step(1)}>Next ›</button>
    {#if action}<button type="button" disabled={busy} onclick={act}>{action.label}</button>{/if}
    <a {href} download={f.name}>Download</a>
    <button type="button" onclick={onclose}>Close</button>
  </div>
  <div class="body">
    {#key f.name}
      <div class="frame" in:fade|local={{ duration: ms(150) }}>
        {#if f.image}
          <img alt={f.name} src={href}>
        {:else if f.content_type === 'application/pdf'}
          <iframe title={f.name} src={href}></iframe>
        {:else}
          <div class="nopreview">
            <span class="glyph">{glyphFor(f.name)}</span>
            <p>No preview for this kind of file.</p>
            <a {href} download={f.name}>Download</a>
          </div>
        {/if}
      </div>
    {/key}
  </div>
</div>

<style>
  /* Purely a transition anchor for the crossfade between files — it must not
     affect layout, so it takes no box of its own. */
  .frame { display: contents; }
</style>
