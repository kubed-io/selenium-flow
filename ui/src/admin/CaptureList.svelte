<script lang="ts">
  import { CAPTURE_OFF, clock } from '../lib/capture'
  import { onMount, untrack } from 'svelte'
  import type { Api } from './api'
  import { CaptureModel } from './capture.svelte'
  import CaptureToolbar from './CaptureToolbar.svelte'

  /* What both panes share below the toolbar. A row arrives as cells: [text, class]
     pairs, drawn as text nodes and nothing else. */
  type Cell = [string, string?]
  let { api, key, root, kind, cells, empty, cleared, open }: {
    api: Api; key: string; root: string; kind: 'console' | 'network'
    cells: (r: never) => Cell[]
    empty: string
    cleared: string
    open?: (r: never) => void
  } = $props()
  const m = untrack(() => new CaptureModel(api, root, key, kind))
  onMount(() => {
    void m.load()
    return () => m.dispose()
  })
</script>

<CaptureToolbar {m} />
{#if m.error}
  <p class="small warn">{m.error}</p>
{:else if m.data && 'how' in m.data}
  <p class="small muted">{CAPTURE_OFF}</p>
{:else if m.data && !m.visible.length}
  <div class="empty">{#if m.clearedAt !== null}Cleared at {clock(m.clearedAt)}<div class="small">{cleared}</div>{:else}{empty}{/if}</div>
{:else}
  {#each m.visible as r (r.seq)}
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions (mouse-only, like the workspace cards) -->
    <div class="crow" onclick={open && (() => open(r as never))}>
      {#each cells(r as never) as [t, c], i (i)}<span class={c}>{t}</span>{/each}
    </div>
  {/each}
{/if}
<p class="small muted">{m.footer}{#if m.dropped}<span class="warn"> · {m.dropped} dropped</span>{/if}</p>
