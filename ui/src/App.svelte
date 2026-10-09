<script lang="ts">
  import {
    App as Host, applyDocumentTheme, applyHostFonts, applyHostStyleVariables,
    type McpUiDisplayMode, type McpUiHostContext,
  } from '@modelcontextprotocol/ext-apps'
  import { onMount, tick, type Component } from 'svelte'
  import { leaf, plural } from './lib/format'
  import ContextView from './lib/views/ContextView.svelte'
  import FilesView from './lib/views/FilesView.svelte'
  import FileView from './lib/views/FileView.svelte'
  import FlowsView from './lib/views/FlowsView.svelte'
  import FlowView from './lib/views/FlowView.svelte'
  import FolderView from './lib/views/FolderView.svelte'
  import SecretsView from './lib/views/SecretsView.svelte'

  type Props = {
    data: never
    uri?: string
    onshow?: (uri: string) => void
    onlink?: (url: string) => void
    expanded?: boolean
    expandable?: boolean
  }
  // By the name a `show` result carries in `component`.
  const VIEWS: Record<string, Component<Props>> = {
    context: ContextView as Component<Props>,
    file: FileView as Component<Props>,
    files: FilesView as Component<Props>,
    folder: FolderView as Component<Props>,
    flows: FlowsView as Component<Props>,
    flow: FlowView as Component<Props>,
    secrets: SecretsView as Component<Props>,
  }
  const EXPANDS = new Set(['flow', 'document'])
  // What Back says: the view it returns to (spec 2026-10-09-show-everything, ruling 12).
  const LABELS: Record<string, string> = { context: 'Workspace', files: 'Files', flows: 'Flows', sites: 'Site data', secrets: 'Secrets' }
  const NOUNS: Record<string, string> = { files: 'kept file', folder: 'file', flows: 'flow', secrets: 'secret' }

  type Shown = { component?: string; uri?: string; data?: unknown }
  type Result = { structuredContent?: unknown; isError?: boolean; content?: { type: string; text?: string }[] }

  let host: Host | undefined
  let connected: Promise<void> | undefined
  let failed = $state<string | null>(null)
  // The model's own show call was refused: its message, in place of a view.
  let refused = $state<string | null>(null)
  // Raw: each change replaces the array, never mutates it.
  let stack = $state.raw<Shown[]>([])
  let error = $state<string | null>(null)
  // The URI a drill-down is waiting on. One at a time: a second click would
  // stack a duplicate view.
  let pending = $state<string | null>(null)
  // Bumped by every stack change and every drill-down; a result that comes
  // back under an older number has been overtaken and is dropped.
  let gen = 0
  let mode = $state<McpUiDisplayMode>('inline')
  let modes = $state.raw<McpUiDisplayMode[]>([])
  let onshow = $state.raw<((uri: string) => void) | undefined>(undefined)
  let onlink = $state.raw<((url: string) => void) | undefined>(undefined)

  const top = $derived(stack.at(-1))
  const View = $derived(top && Object.hasOwn(VIEWS, String(top.component)) ? VIEWS[String(top.component)] : null)
  const below = $derived(stack.at(-2))
  const expanded = $derived(mode === 'fullscreen')
  const expandable = $derived(modes.includes('fullscreen'))
  // Also while fullscreen, so Back to a view that cannot expand still has a way out.
  const toggles = $derived(modes.includes('fullscreen') && (expanded || EXPANDS.has(String(top?.component))))

  function summary(s: Shown) {
    const count = (s.data as { count?: unknown } | undefined)?.count
    const noun = NOUNS[String(s.component)]
    return `Showing ${s.uri}` + (noun && typeof count === 'number' ? `, ${plural(count, noun)}` : '')
  }

  function label(s: Shown | undefined): string {
    const c = String(s?.component)
    const folder = (s?.data as { folder?: unknown } | undefined)?.folder
    if (c === 'folder' && typeof folder === 'string') return folder.charAt(0).toUpperCase() + folder.slice(1)
    if (c === 'document' && s?.uri) return leaf(s.uri)
    return Object.hasOwn(LABELS, c) ? LABELS[c] : 'Back'
  }

  function settle(next: Shown[]) {
    gen++
    stack = next
    error = null
    refused = null
    pending = null
    const t = next.at(-1)
    // Best effort: a host may not take it, and the view does not depend on it.
    if (t) connected?.then(() => host!.updateModelContext({ content: [{ type: 'text', text: summary(t) }] })).catch(() => {})
  }

  async function push(uri: string) {
    if (pending !== null) return
    const mine = ++gen
    pending = uri
    error = null
    try {
      const r = (await host!.callServerTool({ name: 'show', arguments: { uri } })) as Result
      if (mine !== gen) return
      if (r.isError) throw new Error(r.content?.find((c) => c.text)?.text || `Could not show ${uri}.`)
      settle([...stack, (r.structuredContent ?? {}) as Shown])
    } catch (err) {
      if (mine === gen) error = err instanceof Error ? err.message : String(err)
    } finally {
      if (mine === gen) pending = null
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
    void [stack, error, mode, failed, refused]
    tick().then(fit)
  })

  onMount(() => {
    let gone = false
    // What changes inside a view (a deleted tile, an image loading) is not the stack.
    // Next frame, as the SDK does: fitting inside the callback is a resize loop.
    const watch = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => requestAnimationFrame(fit))
    watch?.observe(document.body)
    const onresult = (r: Result) => {
      if (!r.isError) return settle([(r.structuredContent ?? {}) as Shown])
      settle([])
      refused = r.content?.find((c) => c.text)?.text || 'Could not show this.'
    }
    ;(async () => {
      try {
        const h = (host = new Host(
          { name: 'selenium-flow', version: '1' },
          // A host may refuse a mode the app did not declare.
          { availableDisplayModes: ['inline', 'fullscreen'] },
          { autoResize: true },
        ))
        h.addEventListener('toolresult', onresult)
        h.addEventListener('hostcontextchanged', adopt)
        await (connected = h.connect())
        if (gone) return
        adopt(h.getHostContext())
        onshow = h.getHostCapabilities()?.serverTools ? push : undefined
        onlink = h.getHostCapabilities()?.openLinks
          ? (url: string) => { h.openLink({ url }).catch(() => {}) }
          : undefined
      } catch (err) {
        if (!gone) failed = err instanceof Error ? err.message : String(err)
      }
    })()
    return () => {
      gone = true
      watch?.disconnect()
      host?.removeEventListener('toolresult', onresult)
      host?.removeEventListener('hostcontextchanged', adopt)
      host?.close().catch(() => {})
    }
  })
</script>

{#if failed !== null}
  <div class="empty error">This host could not start the app: {failed}</div>
{:else if refused !== null}
  <div class="empty error">{refused}</div>
{:else if !top}
  <div class="empty">Loading…</div>
{:else}
  {#if stack.length > 1 || toggles}
    <div class="nav">
      {#if stack.length > 1}
        <button type="button" aria-label="Back" onclick={() => settle(stack.slice(0, -1))}>← {label(below)}</button>
      {/if}
      <span class="grow"></span>
      {#if toggles}
        {@const label = expanded ? 'Exit fullscreen' : 'Fullscreen'}
        <button type="button" aria-label={label} title={label} onclick={toggle}>{expanded ? '⤡' : '⤢'}</button>
      {/if}
    </div>
  {/if}
  {#if error}<div class="small error line">{error}</div>{/if}
  <div class="view" aria-busy={pending !== null}>
    {#if View}
      {#key top}<View data={top.data as never} uri={top.uri} {onshow} {onlink} {expanded} {expandable} />{/key}
    {:else}
      <div class="empty error">Nothing to show{top.component ? ` for "${top.component}"` : ''}.</div>
    {/if}
  </div>
{/if}

<style>
  .nav { display: flex; align-items: center; gap: var(--gap); margin-bottom: 8px; }
  .nav button { padding: 4px 10px; min-height: 32px; }
  .line { margin-bottom: 8px; }
  /* Waiting on a drill-down: the view dims and stops taking clicks. */
  .view { transition: opacity 0.15s ease; }
  .view[aria-busy=true] { opacity: 0.55; pointer-events: none; }
</style>
