<script lang="ts">
  import { App as Host } from '@modelcontextprotocol/ext-apps'
  import { onMount, type Component } from 'svelte'
  import FileSections from './lib/FileSections.svelte'
  import SessionList from './lib/SessionList.svelte'
  import SessionSummary from './lib/SessionSummary.svelte'

  // By the name a tool result carries in `component`, as `SF[name]` was.
  // No onpick: a click inside someone else's transcript has nowhere to go.
  const COMPONENTS: Record<string, Component<{ data: never }>> = {
    fileSections: FileSections as Component<{ data: never }>,
    sessionList: SessionList as Component<{ data: never }>,
    sessionSummary: SessionSummary as Component<{ data: never }>,
  }

  type View = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'data'; data: Record<string, unknown> }
  // Raw: replaced wholesale by each tool result, never mutated.
  let view = $state.raw<View>({ kind: 'loading' })
  const Shown = $derived(view.kind === 'data' && Object.hasOwn(COMPONENTS, String(view.data.component)) ? COMPONENTS[String(view.data.component)] : null)

  onMount(async () => {
    try {
      const host = new Host({ name: 'selenium-flow', version: '1' })
      host.addEventListener('toolresult', (result) => {
        const r = result as { structuredContent?: Record<string, unknown> }
        view = { kind: 'data', data: (r && (r.structuredContent || (r as Record<string, unknown>))) || {} }
      })
      await host.connect()
    } catch (err) {
      view = { kind: 'error', message: err instanceof Error ? err.message : String(err) }
    }
  })
</script>

{#if view.kind === 'loading'}
  <div class="empty">Loading…</div>
{:else if view.kind === 'error'}
  <div class="empty error">This host could not start the app: {view.message}</div>
{:else if Shown}
  <Shown data={view.data as never} />
{:else}
  <div class="empty error">Nothing to show{view.data.component ? ` for "${view.data.component}"` : ''}.</div>
{/if}
