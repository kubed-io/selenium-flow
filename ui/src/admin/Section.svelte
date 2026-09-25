<script lang="ts">
  import type { Snippet } from 'svelte'

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
      <span class="caret">{open ? '▾' : '▸'}</span>{title}</button>
    <span id={countId} class="pill">{count}</span>
    <span class="grow"></span>
    {@render actions?.()}
  </div>
  <div class="body" id={bodyId}>{@render children()}</div>
</section>
