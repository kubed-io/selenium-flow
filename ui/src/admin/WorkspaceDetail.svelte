<script lang="ts">
  import { onMount, untrack } from 'svelte'
  import Lightbox from '../lib/Lightbox.svelte'
  import WorkspaceSummary from '../lib/WorkspaceSummary.svelte'
  import type { FileEntry, Folder, WorkspacesPayload } from '../lib/types'
  import { DEFAULT_SESSION as DEFAULT, workspacePath, type Api } from './api'
  import LivePane from './LivePane.svelte'
  import { liveUnavailable } from './liveview.svelte'
  import FilesPane from './FilesPane.svelte'
  import { CAPTURE_OFF } from '../lib/capture'
  import CaptureConsolePane from './CaptureConsolePane.svelte'
  import CaptureNetworkPane from './CaptureNetworkPane.svelte'
  import FlowsPane from './FlowsPane.svelte'
  import RequestPane from './RequestPane.svelte'
  import SessionPane from './SessionPane.svelte'
  import HistoryPane from './HistoryPane.svelte'
  import ConfigPane from './ConfigPane.svelte'
  import { ApiError } from './api'
  import type { Live } from './live.svelte'
  import SiteDataPane from './SiteDataPane.svelte'
  import Modal from './Modal.svelte'
  import { Shown, type ModalSpec } from './modal'
  import SessionChips from './SessionChips.svelte'
  import { go, hashes, replace, tabHash, type Tab } from './router.svelte'
  import { WorkspaceModel } from './workspace.svelte'

  let { key, tab, flow, site, request, session, api, live, root, liveScript, viewer }: {
    key: string
    tab: Tab
    flow: string | undefined
    /** The session the address picked; absent reads as the default. */
    session?: string
    /** A Site data host linked from History. */
    site?: string
    /** A request opened from Network. */
    request?: string
    api: Api
    live: Live
    root: string
    /** Where the server serves noVNC's bundle from (BASE + '/live.js'). */
    liveScript: string
    /** The viewer's loader; a test passes its own. */
    viewer?: () => Promise<{ RFB: never }>
  } = $props()

  // Rendered inside {#key route.key}: a workspace switch destroys this whole
  // subtree — its loads, its lightbox, its modal — so nothing here outlives
  // the workspace it was made for.
  const m = untrack(() => new WorkspaceModel(key, api, session || DEFAULT))
  let destroyed = false

  /* The sessions, the default first; one means the page is as it was. The
     picked one is a listed one, or the default: a stale link names nothing. */
  const sessions = $derived(m.row.sessions ?? [])
  const hasSubs = $derived(sessions.length > 1)
  const picked = $derived(!session || session === DEFAULT ? DEFAULT : !m.headed || sessions.some((x) => x.name === session) ? session : DEFAULT)
  const sub = $derived(picked !== DEFAULT)
  const pickedLabel = $derived(sessions.find((x) => x.name === picked)?.label ?? picked)
  /* What the session tabs show: the picked session's own row. */
  const shown = $derived(sub ? (m.sessionRow ?? { key }) : m.row)
  const ready = $derived(sub ? m.sessionRow !== null : m.headed)
  /* The address names the picked session unless it is the default. */
  const pinned = () => (picked === DEFAULT ? null : picked)
  $effect(() => { m.pick(picked) })

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
  // (a confirm clicked as the workspace goes) checks first, and says so.
  const refuseIfGone = () => { if (destroyed) throw new Error('that workspace is no longer on screen') }

  /* Whatever is on screen about ONE browser's downloads, closed when that
     browser goes — a reap noticed in a push, or End browser. */
  function dropBrowserOverlays() {
    if (lightbox?.folder === 'downloads') lightbox = null
    if (modal?.scope === 'downloads') { modal.oncancel?.(); modal = null }
  }

  const vanished = () => {
    flowName = null
    replace(hashes.flows(key), pinned())
  }
  const known = (name: string) => (m.flows?.flows || []).some((f) => f.name === name)

  function openFlow(name: string) {
    flowName = name
    m.flowDoc = null
    // Today's openFlow repainted the whole tab from the listing it had, so
    // neither error on screen outlived a pick.
    m.flowDocError = null
    m.flowsError = null
    replace(hashes.flow(key, name), pinned())
    return m.loadFlow(name)
  }

  const reloadFlows = () => m.loadFlows(() => flowName, vanished)

  /* A move or delete: the flow is no longer here to show. */
  function closeFlow() {
    flowName = null
    replace(hashes.flows(key), pinned())
    return reloadFlows()
  }

  onMount(() => {
    void m.loadConfig()
    void m.loadSession()
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
  let seen: WorkspacesPayload | null = untrack(() => live.data)
  $effect(() => {
    const data = live.data
    if (!data || data === seen) return
    seen = data
    untrack(() => m.onPushed(data, () => flowName, vanished, dropBrowserOverlays))
  })

  const keepPath = (folder: Folder, f: FileEntry, named?: string) =>
    workspacePath(key, '/files/' + encodeURIComponent(folder) + '/' + encodeURIComponent(f.name) + '/keep', named)

  async function keep(folder: Folder, f: FileEntry, named?: string): Promise<boolean> {
    try {
      refuseIfGone()
      await api(keepPath(folder, f, named), 'POST')
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
        await api(workspacePath(key, '/files/' + encodeURIComponent(f.name)), 'DELETE')
        if (resolve) resolve()
        else if (!destroyed) void m.loadFiles()
      },
      oncancel: reject ? () => reject(new Error('cancelled')) : undefined,
    })
  }

  function clear(what: 'downloads' | 'screenshots' | 'recordings') {
    if (what === 'downloads') {
      // Which downloads Files also holds a copy of: the download goes, the
      // copy stays — said per row, the only version both complete and true.
      const kept = new Set(m.files.files.map((f) => f.name))
      // Every session's browser holds its own downloads.
      const subs = Object.keys(m.subDownloads)
      const names = [...m.files.downloads, ...subs.flatMap((n) => m.subDownloads[n])].map((f) => ({ name: f.name, stays: kept.has(f.name) }))
      ask({
        scope: 'downloads', title: 'Clear downloads', body: clearDownloadsBody, data: names, danger: true,
        confirm: names.length ? 'Clear ' + names.length + ' file' + (names.length === 1 ? '' : 's') : 'Clear',
        onconfirm: async () => {
          refuseIfGone()
          await api(workspacePath(key, '/files/downloads'), 'DELETE')
          for (const n of subs) await api(workspacePath(key, '/files/downloads', n), 'DELETE')
          if (!destroyed) void m.loadFiles()
        },
      })
    } else if (what === 'recordings') {
      const names = m.files.recordings.map((f) => f.name)
      ask({
        title: 'Clear recordings', body: clearRecordingsBody, data: names, danger: true,
        confirm: 'Delete ' + names.length + ' recording' + (names.length === 1 ? '' : 's'),
        onconfirm: async () => {
          refuseIfGone()
          await api(workspacePath(key, '/files/recordings'), 'DELETE')
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
          await api(workspacePath(key, '/files/screenshots'), 'DELETE')
          if (!destroyed) void m.loadFiles()
        },
      })
    }
  }

  /* The config's YAML, read again first: the copy on screen can be old. A
     refusal stays in the editor, under what was typed. */
  let configDraft = $state('')
  let configRefusal = $state<string | null>(null)
  async function editConfig() {
    try {
      await m.loadConfig()
    } catch { /* the pane says why */ }
    configDraft = m.config?.text ?? ''
    configRefusal = null
    ask({
      title: 'Edit ' + (m.row.name || key) + ' config', body: configEditor, data: null, confirm: 'Save',
      onconfirm: async () => {
        refuseIfGone()
        try {
          m.showConfig(await api(workspacePath(key, '/config'), 'PUT', { text: configDraft }))
          // Which global flows are offered may have changed.
          if (!destroyed) void reloadFlows()
        } catch (err) {
          if (!(err instanceof ApiError) || err.status !== 400) throw err
          configRefusal = err.message
          throw new Shown(err.message)
        }
      },
    })
  }

  /* The file goes, so the workspace is offered every global flow and expires
     again. */
  function removeConfig() {
    ask({
      title: 'Remove this config?', body: removeConfigBody, data: key + '/workspace.yaml', confirm: 'Remove', danger: true,
      onconfirm: async () => {
        refuseIfGone()
        m.showConfig(await api(workspacePath(key, '/config'), 'DELETE'))
        if (!destroyed) void reloadFlows()
      },
    })
  }

  /* Ending drops the browser, not the workspace: its next open carries on. */
  async function endBrowser() {
    const mine = picked
    if (!confirm(mine === DEFAULT
      ? 'End this browser? The workspace and its context are kept — whatever the browser was holding is lost.'
      : `End the ${pickedLabel} browser? Its session is kept — whatever the browser was holding is lost.`)) return
    try {
      await api(workspacePath(key, '/session', mine), 'DELETE')
    } catch (err) {
      alert('Could not end the browser: ' + (err as Error).message)
      return
    }
    if (destroyed) { void live.load(); return }
    dropBrowserOverlays()
    void m.loadFiles()
    void m.loadSession()
    void m.loadFlows(() => flowName, vanished)
  }

  /* Every session's browser; their records stay. */
  function endAll() {
    const lines = sessions.map((x) => x.label + ' · ' + (x.live ? x.browser || 'browser' : 'idle, no browser'))
    const n = sessions.filter((x) => x.live).length
    ask({
      title: 'End every session’s browser', body: endAllBody, data: lines, danger: true,
      confirm: n ? `End ${n} browser${n === 1 ? '' : 's'}` : 'End all',
      onconfirm: async () => {
        refuseIfGone()
        await api(workspacePath(key, '/end-all'), 'POST')
        if (destroyed) { void live.load(); return }
        dropBrowserOverlays()
        void m.loadFiles()
        void m.loadSession()
        void m.loadFlows(() => flowName, vanished)
      },
    })
  }

  /* A chip changes the session tabs, where the page is now; Files, Flows,
     History and Config ignore it. The default leaves the address. */
  function currentHash(): string {
    if (tab === 'flows' && flowName) return hashes.flow(key, flowName)
    if (tab === 'network' && request) return hashes.request(key, request)
    if (tab === 'site-data' && site) return hashes.site(key, site)
    return tabHash(key, tab)
  }
  const pick = (name: string) => go(currentHash(), name === DEFAULT ? null : name)
  const to = (t: Tab) => go(tabHash(key, t), pinned())
  /* One viewer for all three rows, stepping through the row it was opened
     from, on the list as last answered. */
  function openLightbox(folder: Folder, index: number, named?: string) {
    const listed = () => (named && folder === 'downloads' ? m.subDownloads[named] ?? [] : m.files[folder])
    const files = listed()
    // A browser change can leave the grid drawn while its list is dropped;
    // today's viewer threw on the missing entry, so nothing opens here either.
    if (!files[index]) return
    const lb: Viewer = {
      folder, index, files,
      action: folder === 'files'
        ? { label: '🗑 Delete', run: (f) => new Promise<void>((resolve, reject) => { refuseIfGone(); deleteFile(f, resolve, reject) }) }
        : { label: '📌 Keep', run: async (f) => { refuseIfGone(); await api(keepPath(folder, f, named), 'POST') } },
      // The same index either way: a kept screenshot or a deleted file leaves
      // the list, so the next one slides into its place; a kept download is a
      // copy and stays, so the viewer stays on it.
      refresh: async (at) => {
        if (destroyed) return null
        await m.loadFiles()
        // Its own load may have been overtaken by the poll's (the Keep race).
        await m.filesSettled()
        if (destroyed) return null
        return { files: listed(), index: at }
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
  // The hosts listed, from the loaded payload rather than the last pushed
  // row: a Forget reloads the pane before the next push brings the row up.
  const siteDataTotal = $derived(m.siteDataError || !m.siteData ? '' : String(m.siteData.sites.length))
  const historyTotal = $derived(m.historyError || !m.history ? '' : String(m.history.sites.length))
  const captureOff = $derived(shown.capture === false)
  const consoleTotal = $derived(shown.captured ? String(shown.captured.console) : '')
  const networkTotal = $derived(shown.captured ? String(shown.captured.network) : '')
  const unavailable = $derived(liveUnavailable(shown, live.data?.live_enabled))
  const loadViewer = () => (viewer ?? (() => import(/* @vite-ignore */ liveScript)))()
  const flowsTotal = $derived(!m.flows || m.flowsBlanked ? '' : m.flows.enabled ? String((m.flows.flows || []).length) : 'off')
</script>

{#snippet configEditor()}
  <div class="small muted mono">{key}/workspace.yaml</div>
  <textarea class="yaml" spellcheck="false" bind:value={configDraft}></textarea>
  {#if configRefusal}<p class="small error refusal">{configRefusal}</p>{/if}
{/snippet}

{#snippet removeConfigBody(file: string)}
  <p>The workspace is offered every global flow again, and expires after its last use.</p>
  <ul class="names"><li>{file}</li></ul>
{/snippet}

{#snippet endAllBody(lines: string[])}
  <p>Quits the browser of every session. Their records stay, so open_session picks up where each left off.</p>
  <ul class="names">{#each lines as l (l)}<li>{l}</li>{/each}</ul>
{/snippet}

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

{#snippet clearRecordingsBody(names: string[])}
  <p>Deletes {names.length === 1 ? 'the 1 recording' : 'all ' + names.length + ' recordings'} in this workspace. Anything you kept is in Files and stays.</p>
  <ul class="names">{#each names as n (n)}<li>{n}</li>{/each}</ul>
{/snippet}

{#snippet clearScreenshotsBody(names: string[])}
  <p>Deletes {names.length === 1 ? 'the 1 screenshot' : 'all ' + names.length + ' screenshots'} in this workspace. Anything you kept is in Files and stays.</p>
  <ul class="names">{#each names as n (n)}<li>{n}</li>{/each}</ul>
{/snippet}

<div id="detailView">
  <div class="row" style="margin-bottom:12px">
    <button id="back" onclick={() => go(hashes.list)}>← Workspaces</button>
    <span class="grow"></span>
    {#if !hasSubs}
      <button id="endBrowser" class="danger" disabled={!shown.attached}
              title="Quit this browser. The workspace and its context are kept." onclick={endBrowser}>End browser</button>
    {/if}
  </div>
  <!-- Blank until this workspace's row arrives, from its load or a push. -->
  <div id="detailHeader">{#if m.headed}<WorkspaceSummary data={m.row} />{/if}</div>
  {#if m.config?.error}
    <div id="configInvalid" class="card row">
      <span class="grow small">This workspace's config is invalid: {m.config.error}. Calls naming it are refused until it is fixed.</span>
      <button type="button" class="danger" onclick={editConfig}>Edit YAML</button>
    </div>
  {/if}
  {#if hasSubs}
    <div id="sessionBar" class="row">
      <SessionChips {sessions} {picked} onpick={pick} />
      <span class="grow"></span>
      <button id="endBrowser" class="danger" disabled={!shown.attached}
              title="Quit this session's browser. Its session is kept." onclick={endBrowser}>End {pickedLabel}</button>
      <button id="endAll" onclick={endAll}>End all</button>
    </div>
  {/if}

  <!-- The session tabs, a divider, then the workspace's: only the first five follow the chips. -->
  <div class="tabs subtabs" id="workspaceTabs" role="tablist">
    <button id="tabSession" role="tab" aria-selected={tab === 'session'} onclick={() => to('session')}>Session</button>
    <button id="tabLive" role="tab" aria-selected={tab === 'live'} aria-disabled={!!unavailable} title={unavailable ?? undefined} class:muted={!!unavailable}
            onclick={() => { if (!unavailable) to('live') }}>Live</button>
    <button id="tabConsole" role="tab" aria-selected={tab === 'console'} aria-disabled={captureOff} title={captureOff ? CAPTURE_OFF : undefined} class:muted={captureOff}
            onclick={() => { if (!captureOff) to('console') }}>Console <span id="consoleTotal" class="count">{consoleTotal}</span></button>
    <button id="tabNetwork" role="tab" aria-selected={tab === 'network'} aria-disabled={captureOff} title={captureOff ? CAPTURE_OFF : undefined} class:muted={captureOff}
            onclick={() => { if (!captureOff) to('network') }}>Network <span id="networkTotal" class="count">{networkTotal}</span></button>
    <button id="tabSiteData" role="tab" aria-selected={tab === 'site-data'} onclick={() => to('site-data')}>Site data <span id="siteDataTotal" class="count">{siteDataTotal}</span></button>
    <span class="divider" aria-hidden="true"></span>
    <button id="tabFiles" role="tab" aria-selected={tab === 'files'} onclick={() => to('files')}>Files <span id="filesTotal" class="count">{filesTotal}</span></button>
    <button id="tabFlows" role="tab" aria-selected={tab === 'flows'} onclick={() => to('flows')}>Flows <span id="flowsTotal" class="count">{flowsTotal}</span></button>
    <button id="tabHistory" role="tab" aria-selected={tab === 'history'} onclick={() => to('history')}>History <span id="historyTotal" class="count">{historyTotal}</span></button>
    <button id="tabConfig" role="tab" aria-selected={tab === 'config'} onclick={() => to('config')}>Config</button>
  </div>

  <!-- A sub-session is marked by its chip and this edge, never a line of text. -->
  <div id="sessionArea" class:sub>
    <!-- Where a workspace opens. Blank until its row arrives, like the header:
         a default drawn before then would claim a browser nobody asked for. -->
    {#if ready}
      <SessionPane row={shown} hidden={tab !== 'session'} label={hasSubs ? pickedLabel : null} />
    {:else}
      <div id="paneSession" class="empty" hidden={tab !== 'session'}>Loading…</div>
    {/if}
    <SiteDataPane {m} {api} {ask} {refuseIfGone} isGone={() => destroyed} {site} hidden={tab !== 'site-data'} />
    <!-- Not kept mounted: leaving the tab is what closes a live stream. -->
    {#if tab === 'live'}
      <div id="paneLive">
        {#if !ready}
          <p class="empty">Loading…</p>
        {:else if unavailable}
          <p class="empty">{unavailable}</p>
        {:else}
          {#key picked}
            <LivePane {api} {key} session={picked} name={m.row.name ?? key} {root} load={loadViewer} />
          {/key}
        {/if}
      </div>
    {/if}
    {#if tab === 'console' || tab === 'network'}
      <div id="paneCapture">
        {#if captureOff}
          <p class="small muted">{CAPTURE_OFF}</p>
        {:else}
          {#key picked}
            {#if tab === 'console'}
              <CaptureConsolePane {api} {key} {root} session={picked} />
            {:else if request}
              <RequestPane {api} {key} id={request} session={picked} />
            {:else}
              <CaptureNetworkPane {api} {key} {root} session={picked} />
            {/if}
          {/key}
        {/if}
      </div>
    {/if}
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
  <HistoryPane {m} {api} {ask} {refuseIfGone} isGone={() => destroyed} hidden={tab !== 'history'} />
  <ConfigPane {m} hidden={tab !== 'config'} onedit={editConfig} onremove={removeConfig} />
</div>

<style>
  #configInvalid { margin-top: 12px; border-color: var(--warn); color: var(--warn); }
  #sessionBar { margin-top: 12px; gap: 8px; flex-wrap: wrap; }
  .divider { align-self: stretch; width: 1px; margin: 6px 6px; background: var(--line); }
  #sessionArea.sub { border-left: 3px solid var(--accent); padding-left: 9px; margin-left: -12px; }
</style>

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
