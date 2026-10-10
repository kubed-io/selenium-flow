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
  /* A live row that was not kept (a static request, a debug entry) has no
     detail to open: the server never kept it. */
  const opens = (r: { kept?: boolean }) => (open && r.kept !== false ? () => open(r as never) : undefined)
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
{:else}
  {@const none = m.mode === 'captured' && !m.visible.length}
  {#if m.visible.length || m.clearedAt !== null}
    <div class="clist">
      {#each m.visible as r (r.seq)}
        {@const go = opens(r)}
        <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions (mouse-only, like the workspace cards) -->
        <div class={['crow', go && 'click']} onclick={go}>
          {#each cells(r as never) as [t, c], i (i)}<span class={c}>{t}</span>{/each}
        </div>
      {:else}
        <div class="empty"><strong>Cleared at {clock(m.clearedAt ?? 0)}</strong><span class="small muted">{cleared}</span></div>
      {/each}
    </div>
  {/if}
  {#if none}
    <p class="small muted">{empty}</p>
  {:else}
    <p class="small muted">{m.footer}{#if m.dropped}<span class="warn"> · {m.dropped} dropped</span>{/if}</p>
  {/if}
{/if}
