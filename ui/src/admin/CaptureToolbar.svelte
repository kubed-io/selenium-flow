<script lang="ts">
  import type { CaptureModel } from './capture.svelte'

  let { m }: { m: CaptureModel } = $props()
  const chips = $derived(m.kind === 'console'
    ? [['all', 'All'], ['errors', 'Errors'], ['warnings', 'Warnings']]
    : [['all', 'All'], ['xhr', 'XHR/fetch'], ['failed', 'Failed']])
  const live = $derived(m.mode === 'live')
</script>

<div class="row toolbar">
  <button onclick={() => m.clear()}>Clear</button>
  {#each chips as [k, label] (k)}
    <button class="chip" aria-pressed={m.filter === k} onclick={() => (m.filter = k)}>{label}{live ? '' : '  ' + m.counts[k]}</button>
  {/each}
  <input type="text" placeholder={m.kind === 'console' ? 'Filter messages' : 'Filter URLs'} bind:value={m.text} />
  {#if !live}
    <select aria-label="Pages" bind:value={m.pages} onchange={() => void m.load()}>
      {#each [1, 2, 3, 4, 5] as n (n)}<option value={n}>{n === 1 ? 'This page' : `Last ${n} pages`}</option>{/each}
    </select>
  {/if}
  <span class="grow"></span>
  {#if live}<button onclick={() => (m.paused ? m.resume() : m.pause())}>{m.paused ? 'Resume' : 'Pause'}</button>{/if}
  <span class="switch">
    <button aria-pressed={!live} onclick={() => void m.captured()}>Captured</button>
    <button aria-pressed={live} onclick={() => m.live()}>{live ? (m.paused ? '● Paused' : '● Live') : 'Live'}</button>
  </span>
</div>
