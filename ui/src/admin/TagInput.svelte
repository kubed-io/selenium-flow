<script lang="ts">
  import { tick } from 'svelte'
  import { fold } from './tags'

  /* `+ tag`, which opens an input offering the tags in use with their counts,
     narrowed as the admin types. Arrows move through the offers and Enter
     takes the one marked, else what was typed; a click takes an offer. Escape
     or leaving the input closes it. */
  let { offered, taken, onadd }: {
    offered: [string, number][]
    taken: string[]
    onadd: (tag: string) => void
  } = $props()

  const id = 'offer-' + Math.random().toString(36).slice(2)
  let open = $state(false)
  let typed = $state('')
  let hi = $state(-1)
  let input = $state<HTMLInputElement>()
  const shown = $derived(offered.filter(([t]) => !taken.includes(t) && t.includes(fold(typed))))

  async function start() {
    typed = ''
    hi = -1
    open = true
    await tick()
    input?.focus()
  }
  function take(tag: string) {
    open = false
    if (tag && !taken.includes(tag)) onadd(tag)
  }
  function key(e: KeyboardEvent) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const step = e.key === 'ArrowDown' ? 1 : -1
      hi = shown.length ? (hi + step + shown.length) % shown.length : -1
    } else if (e.key === 'Enter') {
      e.preventDefault()
      take(hi >= 0 && hi < shown.length ? shown[hi][0] : fold(typed))
    } else if (e.key === 'Escape') open = false
  }
</script>

{#if open}<span class="tagadd"><input bind:this={input} bind:value={typed} onkeydown={key} oninput={() => { hi = -1 }}
  onblur={() => { open = false }} role="combobox" aria-label="Tag" aria-expanded={shown.length > 0} aria-controls={id}
  aria-activedescendant={hi >= 0 ? id + '-' + hi : undefined} spellcheck="false" autocomplete="off" />{#if shown.length}<ul
  class="offer" role="listbox" {id}>{#each shown as [t, n], i (t)}<!--
  svelte-ignore a11y_click_events_have_key_events (the input owns the keys: arrows and Enter)
  --><li id={id + '-' + i} role="option" aria-selected={i === hi}
  onmousedown={(e) => e.preventDefault()} onclick={() => take(t)}><span>{t}</span><span class="n">{n}</span></li>{/each}</ul>{/if}</span>{:else}<button
  type="button" class="addtag" onclick={start}>+ tag</button>{/if}
