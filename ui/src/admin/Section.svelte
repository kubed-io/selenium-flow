<script lang="ts">
  import type { Snippet } from 'svelte'
  import { slide } from 'svelte/transition'
  import { ms } from '../motion'

  let { id, title, count, children, actions }: {
    id: string
    title: string
    count: string | number
    children: Snippet
    actions?: Snippet
  } = $props()
  let open = $state(true)
  const bodyId = $derived(id.replace(/Section$/, 'Body'))
  const countId = $derived(id.replace(/Section$/, 'Count'))
</script>

<section class="section" {id} data-open={String(open)}>
  <div class="head">
    <!-- A button, not a styled span: the only way to open or close the section,
         so it must be reachable by keyboard and announce its state. -->
    <button type="button" class="title" aria-expanded={open} aria-controls={bodyId} onclick={() => (open = !open)}>
      <span class="caret" style="transform: rotate({open ? 0 : -90}deg)">▾</span>{title}</button>
    <span id={countId} class="pill">{count}</span>
    <span class="grow"></span>
    {@render actions?.()}
  </div>
  <div class="body" id={bodyId}>
    {#if open}<div transition:slide|local={{ duration: ms(150) }}>{@render children()}</div>{/if}
  </div>
</section>

<style>
  .caret { transition: transform 150ms; }
  @media (prefers-reduced-motion: reduce) {
    .caret { transition: none; }
  }
</style>
