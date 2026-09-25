<script lang="ts">
  import { onMount, untrack } from 'svelte'
  import Lightbox from '../lib/Lightbox.svelte'
  import SessionSummary from '../lib/SessionSummary.svelte'
  import type { FileEntry, Folder, SessionsPayload } from '../lib/types'
  import { sessionPath, type Api } from './api'
  import FilesPane from './FilesPane.svelte'
  import FlowsPane from './FlowsPane.svelte'
  import type { Live } from './live.svelte'
  import Modal from './Modal.svelte'
  import type { ModalSpec } from './modal'
  import { go, hashes, replace } from './router.svelte'
  import { SessionModel } from './session.svelte'

  let { key, tab, flow, api, live, root }: {
    key: string
    tab: 'files' | 'flows'
    flow: string | undefined
    api: Api
    live: Live
    root: string
  } = $props()

  // Rendered inside {#key route.key}: a session switch destroys this whole
  // subtree — its loads, its lightbox, its modal — so nothing here outlives
  // the session it was made for.
  const m = untrack(() => new SessionModel(key, api))
  let destroyed = false

  // Stored erased: each spec's body snippet is typed to its own data, and only
  // that pairing matters, which `ask` checks at the call site.
  type AnyModal = ModalSpec<never>
  let modal = $state.raw<AnyModal | null>(null)

  interface Viewer {
    folder: Folder
    index: number
    files: FileEntry[]
    action: { label: string; run: (f: FileEntry) => Promise<unknown> }
    refresh: (at: number) => Promise<{ files: FileEntry[]; index: number } | null>
    onclose: () => void
  }
  // Raw, so a callback can tell whether the viewer it belongs to is still up.
  let lightbox = $state.raw<Viewer | null>(null)
  let flowName = $state<string | null>(null)

  /* One modal at a time. Each closes itself by identity, as today's
     `activeModal === box` did: a box dropped while its confirm was in flight
     must not, when that confirm lands, close whichever box replaced it. */
  function ask<T>(spec: ModalSpec<T>) {
    modal?.oncancel?.()
    const mine: ModalSpec<T> = {
      ...spec,
      onconfirm: async () => {
        await spec.onconfirm()
        if ((modal as unknown) === mine) modal = null
      },
      oncancel: () => {
        spec.oncancel?.()
        if ((modal as unknown) === mine) modal = null
      },
    }
    modal = mine as unknown as AnyModal
  }

  // The backstop the structure cannot give: a handler held past the switch
  // (a confirm clicked as the session goes) checks first, and says so.
  const refuseIfGone = () => { if (destroyed) throw new Error('that session is no longer on screen') }

  /* Whatever is on screen about ONE browser's downloads, closed when that
     browser goes — a reap noticed in a push, or End browser. */
  function dropBrowserOverlays() {
    if (lightbox?.folder === 'downloads') lightbox = null
    if (modal?.scope === 'downloads') { modal.oncancel?.(); modal = null }
  }

  const vanished = () => {
    flowName = null
    replace(hashes.flows(key))
  }
  const known = (name: string) => (m.flows?.flows || []).some((f) => f.name === name)

  function openFlow(name: string) {
    flowName = name
    m.flowDoc = null
    // Today's openFlow repainted the whole tab from the listing it had, so
    // neither error on screen outlived a pick.
    m.flowDocError = null
    m.flowsError = null
    replace(hashes.flow(key, name))
    return m.loadFlow(name)
  }

  const reloadFlows = () => m.loadFlows(() => flowName, vanished)

  /* A move or delete: the flow is no longer here to show. */
  function closeFlow() {
    flowName = null
    replace(hashes.flows(key))
    return reloadFlows()
  }

  onMount(() => {
    void (async () => {
      await m.loadFiles()
      if (destroyed) return
      await m.loadFlows(() => flowName, vanished)
      // A push can overtake that load, which then returns unpainted.
      await m.flowsSettled()
      if (destroyed) return
      // A deep link opens a flow only once the listing says it exists (D5).
      if (flow && flow !== flowName && known(flow)) await openFlow(flow)
    })()
    return () => {
      destroyed = true
      m.dispose()
      modal?.oncancel?.()
    }
  })

  // The hash edited by hand while here.
  $effect(() => {
    const f = flow
    untrack(() => { if (f && f !== flowName && m.flows && known(f)) void openFlow(f) })
  })

  // Pushed updates. The first value is what the list already had.
  let seen: SessionsPayload | null = untrack(() => live.data)
  $effect(() => {
    const data = live.data
    if (!data || data === seen) return
    seen = data
    untrack(() => m.onPushed(data, () => flowName, vanished, dropBrowserOverlays))
  })

  const keepPath = (folder: Folder, f: FileEntry) =>
    sessionPath(key, '/files/' + encodeURIComponent(folder) + '/' + encodeURIComponent(f.name) + '/keep')

  async function keep(folder: Folder, f: FileEntry): Promise<boolean> {
    try {
      refuseIfGone()
      await api(keepPath(folder, f), 'POST')
    } catch (err) {
      alert('Could not keep that file: ' + (err as Error).message)
      return false
    }
    if (!destroyed) void m.loadFiles()
    return true
  }

  function deleteFile(f: FileEntry, resolve?: () => void, reject?: (e: Error) => void) {
    ask({
      title: 'Delete a file', body: deleteBody, data: f.name, confirm: 'Delete', danger: true,
      onconfirm: async () => {
        refuseIfGone()
        await api(sessionPath(key, '/files/' + encodeURIComponent(f.name)), 'DELETE')
        if (resolve) resolve()
        else if (!destroyed) void m.loadFiles()
      },
      oncancel: reject ? () => reject(new Error('cancelled')) : undefined,
    })
  }

  function clear(what: 'downloads' | 'screenshots') {
    if (what === 'downloads') {
      // Which downloads Files also holds a copy of: the download goes, the
      // copy stays — said per row, the only version both complete and true.
      const kept = new Set(m.files.files.map((f) => f.name))
      const names = m.files.downloads.map((f) => ({ name: f.name, stays: kept.has(f.name) }))
      ask({
        scope: 'downloads', title: 'Clear downloads', body: clearDownloadsBody, data: names, danger: true,
        confirm: names.length ? 'Clear ' + names.length + ' file' + (names.length === 1 ? '' : 's') : 'Clear',
        onconfirm: async () => {
          refuseIfGone()
          await api(sessionPath(key, '/files/downloads'), 'DELETE')
          if (!destroyed) void m.loadFiles()
        },
      })
    } else {
      const names = m.files.screenshots.map((f) => f.name)
      ask({
        title: 'Clear screenshots', body: clearScreenshotsBody, data: names, danger: true,
        confirm: 'Delete ' + names.length + ' screenshot' + (names.length === 1 ? '' : 's'),
        onconfirm: async () => {
          refuseIfGone()
          await api(sessionPath(key, '/files/screenshots'), 'DELETE')
          if (!destroyed) void m.loadFiles()
        },
      })
    }
  }

  /* Ending drops the browser, not the session: its next open carries on. */
  async function endBrowser() {
    if (!confirm('End this browser? The session and its context are kept — whatever the browser was holding is lost.')) return
    try {
      await api(sessionPath(key), 'DELETE')
    } catch (err) {
      alert('Could not end the browser: ' + (err as Error).message)
      return
    }
    if (destroyed) { void live.load(); return }
    dropBrowserOverlays()
    void m.loadFiles()
    void m.loadFlows(() => flowName, vanished)
  }

  /* One viewer for all three rows, stepping through the row it was opened
     from, on the list as last answered. */
  function openLightbox(folder: Folder, index: number) {
    const files = m.files[folder]
    // A browser change can leave the grid drawn while its list is dropped;
    // today's viewer threw on the missing entry, so nothing opens here either.
    if (!files[index]) return
    const lb: Viewer = {
      folder, index, files,
      action: folder === 'files'
        ? { label: '🗑 Delete', run: (f) => new Promise<void>((resolve, reject) => { refuseIfGone(); deleteFile(f, resolve, reject) }) }
        : { label: '📌 Keep', run: async (f) => { refuseIfGone(); await api(keepPath(folder, f), 'POST') } },
      // The same index either way: a kept screenshot or a deleted file leaves
      // the list, so the next one slides into its place; a kept download is a
      // copy and stays, so the viewer stays on it.
      refresh: async (at) => {
        if (destroyed) return null
        await m.loadFiles()
        // Its own load may have been overtaken by the poll's (the Keep race).
        await m.filesSettled()
        if (destroyed) return null
        return { files: m.files[folder], index: at }
      },
      // Only its own viewer: a refresh that lands after this one was closed
      // must not close the one opened since.
      onclose: () => { if (lightbox === lb) lightbox = null },
    }
    lightbox = lb
  }

  // Interpolated because Svelte trims a leading space from a tag's own text,
  // and today's page reads "a.png — gone", not "a.png— gone".
  const STAYS = ' — copy in Files stays'
  const GONE = ' — gone'

  const filesTotal = $derived(m.filesBlanked ? '' : String(m.row.files_count ?? ''))
  const flowsTotal = $derived(!m.flows || m.flowsBlanked ? '' : m.flows.enabled ? String((m.flows.flows || []).length) : 'off')
</script>

{#snippet deleteBody(name: string)}
  <p>This removes it from Files for good. There is no undo.</p>
  <ul class="names"><li>{name}</li></ul>
{/snippet}

{#snippet clearDownloadsBody(names: { name: string; stays: boolean }[])}
  <p>Deletes what this browser downloaded. The Grid has no per-file delete, so this clears all of them.</p>
  {#if names.length}
    <ul class="names">
      {#each names as n (n.name)}
        <li>{n.name}{#if n.stays}<span class="fate stays">{STAYS}</span>{:else}<span class="fate gone">{GONE}</span>{/if}</li>
      {/each}
    </ul>
  {:else}
    <p class="small muted">There is nothing downloaded to clear.</p>
  {/if}
{/snippet}

{#snippet clearScreenshotsBody(names: string[])}
  <p>Deletes {names.length === 1 ? 'the 1 screenshot' : 'all ' + names.length + ' screenshots'} in this session. Anything you kept is in Files and stays.</p>
  <ul class="names">{#each names as n (n)}<li>{n}</li>{/each}</ul>
{/snippet}

<div id="detailView">
  <div class="row" style="margin-bottom:12px">
    <button id="back" onclick={() => go(hashes.list)}>← Sessions</button>
    <span class="grow"></span>
    <button id="endBrowser" class="danger" disabled={!m.row.attached}
            title="Quit this browser. The session and its context are kept." onclick={endBrowser}>End browser</button>
  </div>
  <!-- Blank until this session's row arrives, from its load or a push. -->
  <div id="detailHeader">{#if m.headed}<SessionSummary data={m.row} />{/if}</div>

  <div class="tabs subtabs" id="sessionTabs" role="tablist">
    <button id="tabFiles" role="tab" aria-selected={tab === 'files'} onclick={() => go(hashes.session(key))}>Files <span id="filesTotal" class="count">{filesTotal}</span></button>
    <button id="tabFlows" role="tab" aria-selected={tab === 'flows'} onclick={() => go(hashes.flows(key))}>Flows <span id="flowsTotal" class="count">{flowsTotal}</span></button>
  </div>

  <!-- Both panes stay mounted and toggle `hidden`, as today: a section
       collapsed on Files is still collapsed after a look at Flows. -->
  <FilesPane {m} {root} hidden={tab !== 'files'}
             onopen={openLightbox} onkeep={keep} ondelete={(f) => deleteFile(f)} onclear={clear} />
  <div id="paneFlows" hidden={tab !== 'flows'}>
    <!-- Today's box around #flows; no accordion in it (W9). -->
    <div class="section"><div class="body"><FlowsPane
      {m} {flowName} {api} {ask} {refuseIfGone} isGone={() => destroyed}
      onpick={(name) => void openFlow(name)} onclosed={closeFlow} reload={reloadFlows} /></div></div>
  </div>
</div>

<!-- An each of one, keyed by the viewer, not {#if}: a viewer's props must
     stay bound to it. An action still finishing after its viewer closed reads
     `refresh` and `onclose` late, and through {#if} those reads reached
     whichever viewer had been opened since, and closed it. -->
{#each lightbox ? [lightbox] : [] as lb (lb)}
  <Lightbox files={lb.files} index={lb.index} base={root}
            action={lb.action} refresh={lb.refresh} onclose={lb.onclose} />
{/each}
{#if modal}
  <!-- Keyed: a box that replaces another starts fresh, not busy. It closes
       itself by identity (see `ask`), so its own close report is not needed. -->
  {#key modal}
    <Modal spec={modal} onclosed={() => {}} />
  {/key}
{/if}
