<script lang="ts">
  import { untrack, type Snippet } from 'svelte'
  import { slide } from 'svelte/transition'
  import { ms } from '../motion'
  import { folds } from './folds.svelte'

  let { id, title, count, children, actions }: {
    id: string
    title: string
    count: string | number
    children: Snippet
    actions?: Snippet
  } = $props()
  // Persisted across remounts by id, in `folds` — the static page's sections
  // never remounted at all, so a fold stayed closed for the page's life; a
  // remounted Section (SessionDetail keys on the session) must start exactly
  // as it was left, not reopened. `untrack`: read once, at mount, on purpose
  // — `id` doesn't change under a live Section, and reopening later must go
  // through `toggle()`, not a re-read of `folds` chasing some other tab's fold.
  let open = $state(untrack(() => folds[id] ?? true))
  // `data-open` (and so the global `.section[data-open=false] > .body {
  // display: none }` rule) has to follow what's actually on screen, not the
  // target state: setting it to "false" the instant `open` flips would hide
  // the body before the slide's outro ever paints, snapping the accordion
  // shut instead of sliding it. So it follows `shown` instead: opening shows
  // it immediately (before the intro runs), closing waits for the outro to
  // finish. `open` itself still drives `aria-expanded`, the caret and the
  // `{#if}` right away. It starts at `open` too (also read once), so a
  // remounted, folded section starts with no body and no intro to fake.
  let shown = $state(untrack(() => open))
  const bodyId = $derived(id.replace(/Section$/, 'Body'))
  const countId = $derived(id.replace(/Section$/, 'Count'))

  function toggle() {
    open = !open
    folds[id] = open
    if (open) shown = true
  }
</script>

<section class="section" {id} data-open={String(shown)}>
  <div class="head">
    <!-- A button, not a styled span: the only way to open or close the section,
         so it must be reachable by keyboard and announce its state. -->
    <button type="button" class="title" aria-expanded={open} aria-controls={bodyId} onclick={toggle}>
      <span class="caret" style="transform: rotate({open ? 0 : -90}deg)">▾</span>{title}</button>
    <span id={countId} class="pill">{count}</span>
    <span class="grow"></span>
    {@render actions?.()}
  </div>
  <div class="body" id={bodyId}>
    {#if open}
      <div transition:slide|local={{ duration: ms(150) }} onoutroend={() => (shown = open)}>{@render children()}</div>
    {/if}
  </div>
</section>

<style>
  .caret { transition: transform 150ms; }
  @media (prefers-reduced-motion: reduce) {
    .caret { transition: none; }
  }
</style>
