<script lang="ts">
  import {
    App as Host, applyDocumentTheme, applyHostFonts, applyHostStyleVariables,
    type McpUiDisplayMode, type McpUiHostContext,
  } from '@modelcontextprotocol/ext-apps'
  import { onMount, tick, type Component } from 'svelte'
  import SessionList from './lib/SessionList.svelte'
  import SessionSummary from './lib/SessionSummary.svelte'
  import ContextView from './lib/views/ContextView.svelte'
  import FilesView from './lib/views/FilesView.svelte'
  import FlowsView from './lib/views/FlowsView.svelte'
  import FlowView from './lib/views/FlowView.svelte'
  import FolderView from './lib/views/FolderView.svelte'

  type Props = { data: never; onshow?: (uri: string) => void; expanded?: boolean }
  // By the name a `show` result carries in `component`.
  const VIEWS: Record<string, Component<Props>> = {
    context: ContextView as Component<Props>,
    files: FilesView as Component<Props>,
    folder: FolderView as Component<Props>,
    flows: FlowsView as Component<Props>,
    flow: FlowView as Component<Props>,
    sessionList: SessionList as Component<Props>,
    sessionSummary: SessionSummary as Component<Props>,
  }
  const EXPANDS = new Set(['flow'])
  const NOUNS: Record<string, string> = { files: 'kept files', folder: 'files', flows: 'flows' }

  type Shown = { component?: string; uri?: string; data?: unknown }
  type Result = { structuredContent?: unknown; isError?: boolean; content?: { type: string; text?: string }[] }

  let host: Host | undefined
  let connected: Promise<void> | undefined
  let failed = $state<string | null>(null)
  // Raw: each change replaces the array, never mutates it.
  let stack = $state.raw<Shown[]>([])
  let error = $state<string | null>(null)
  let mode = $state<McpUiDisplayMode>('inline')
  let modes = $state.raw<McpUiDisplayMode[]>([])
  let onshow = $state.raw<((uri: string) => void) | undefined>(undefined)

  const top = $derived(stack.at(-1))
  const View = $derived(top && Object.hasOwn(VIEWS, String(top.component)) ? VIEWS[String(top.component)] : null)
  const expanded = $derived(mode === 'fullscreen')
  // Also while fullscreen, so Back to a view that cannot expand still has a way out.
  const toggles = $derived(modes.includes('fullscreen') && (expanded || EXPANDS.has(String(top?.component))))

  function summary(s: Shown) {
    const count = (s.data as { count?: unknown } | undefined)?.count
    const noun = NOUNS[String(s.component)]
    return `Showing ${s.uri}` + (noun && typeof count === 'number' ? `, ${count} ${noun}` : '')
  }

  function settle(next: Shown[]) {
    stack = next
    error = null
    const t = next.at(-1)
    // Best effort: a host may not take it, and the view does not depend on it.
    if (t) connected?.then(() => host!.updateModelContext({ content: [{ type: 'text', text: summary(t) }] })).catch(() => {})
  }

  async function push(uri: string) {
    try {
      const r = (await host!.callServerTool({ name: 'show', arguments: { uri } })) as Result
      if (r.isError) throw new Error(r.content?.find((c) => c.text)?.text || `Could not show ${uri}.`)
      settle([...stack, (r.structuredContent ?? {}) as Shown])
    } catch (err) {
      error = err instanceof Error ? err.message : String(err)
    }
  }

  async function toggle() {
    try {
      mode = (await host!.requestDisplayMode({ mode: expanded ? 'inline' : 'fullscreen' })).mode
    } catch {
      // The host said no; stay as we are.
    }
  }

  function adopt(ctx: McpUiHostContext | undefined) {
    if (!ctx) return
    if (ctx.theme) applyDocumentTheme(ctx.theme)
    if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables)
    if (ctx.styles?.css?.fonts) applyHostFonts(ctx.styles.css.fonts)
    if (ctx.safeAreaInsets) document.documentElement.style.setProperty('--safe-left', `${ctx.safeAreaInsets.left}px`)
    if (ctx.displayMode) mode = ctx.displayMode
    if (ctx.availableDisplayModes) modes = ctx.availableDisplayModes
  }

  // claude.ai ignores size-changed and reads the document's own height, so the
  // shell sets it as well as letting autoResize report it.
  function fit() {
    const root = document.documentElement
    root.style.height = 'max-content'
    root.style.height = `${Math.ceil(root.getBoundingClientRect().height)}px`
  }

  // Read to be tracked: whatever changes what the shell draws.
  $effect(() => {
    void [stack, error, mode, failed]
    tick().then(fit)
  })

  onMount(() => {
    // What changes inside a view (a deleted tile, a paged row) is not the stack.
    const watch = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(fit)
    watch?.observe(document.body)
    ;(async () => {
      try {
        const h = (host = new Host({ name: 'selenium-flow', version: '1' }, {}, { autoResize: true }))
        h.addEventListener('toolresult', (r) => settle([((r as Result).structuredContent ?? {}) as Shown]))
        h.addEventListener('hostcontextchanged', adopt)
        await (connected = h.connect())
        adopt(h.getHostContext())
        onshow = h.getHostCapabilities()?.serverTools ? push : undefined
      } catch (err) {
        failed = err instanceof Error ? err.message : String(err)
      }
    })()
    return () => watch?.disconnect()
  })
</script>

{#if failed !== null}
  <div class="empty error">This host could not start the app: {failed}</div>
{:else if !top}
  <div class="empty">Loading…</div>
{:else}
  {#if stack.length > 1 || toggles}
    <div class="nav">
      {#if stack.length > 1}
        <button type="button" aria-label="Back" onclick={() => settle(stack.slice(0, -1))}>← Back</button>
      {/if}
      <span class="grow"></span>
      {#if toggles}
        {@const label = expanded ? 'Exit fullscreen' : 'Fullscreen'}
        <button type="button" aria-label={label} title={label} onclick={toggle}>{expanded ? '⤡' : '⤢'}</button>
      {/if}
    </div>
  {/if}
  {#if error}<div class="small error line">{error}</div>{/if}
  {#if View}
    <View data={top.data as never} {onshow} {expanded} />
  {:else}
    <div class="empty error">Nothing to show{top.component ? ` for "${top.component}"` : ''}.</div>
  {/if}
{/if}

<style>
  .nav { display: flex; align-items: center; gap: var(--gap); margin-bottom: 8px; }
  .nav button { padding: 4px 10px; min-height: 32px; }
  .line { margin-bottom: 8px; }
</style>
