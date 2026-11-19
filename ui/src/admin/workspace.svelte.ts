import type { Api } from './api'
import { DEFAULT_SESSION, workspacePath } from './api'
import { Latest } from './latest'
import type { ConfigPayload, FileEntry, FilesData, FilesResponse, FlowDoc, FlowsListing, HistoryPayload, WorkspaceRow, SiteDataPayload, WorkspacesPayload } from '../lib/types'

/* Handed to a workspace that has not answered yet, or whose load failed: the
   clears must never act on a previous workspace's names. */
export const NO_FILES: FilesData = Object.freeze({ downloads: [], screenshots: [], recordings: [], files: [], browser: false }) as FilesData

/* Not a count: deleting the kept copy of a name that is also a download leaves
   the count alone while the tile changes. The browser id is in it because a
   different browser is a different file store. */
export const filesStamp = (row: WorkspaceRow) => (row.session_id || '-') + ':' + row.files_rev

const downloadsEmpty = (hasBrowser: boolean) => (hasBrowser ? 'No downloads.' : 'No browser — downloads go with it.')

export interface FilesView {
  downloads: FileEntry[]
  screenshots: FileEntry[]
  recordings: FileEntry[]
  files: FileEntry[]
  downloadsEmpty: string
  /* The pills, set only by a load that answers — as today, a browser change
     blanks the Downloads grid but leaves its pill until the reload lands. */
  counts: { downloads: number; screenshots: number; recordings: number; files: number }
}

/* One workspace on screen. Created by WorkspaceDetail and disposed with it, so
   nothing it loads can land on another workspace's page. */
export class WorkspaceModel {
  // Raw state throughout: every value is replaced wholesale, and `files` is
  // compared by identity with NO_FILES — a deep proxy would never be equal.
  row = $state.raw<WorkspaceRow>({ key: '' })
  /* The header stays blank until a row arrives, from a load or a push. */
  headed = $state(false)
  /* What the actions act on: the clears' lists, the lightbox's list. */
  files = $state.raw<FilesData>(NO_FILES)
  /* What the three rows show; null while the first load is out. Separate from
     `files` because a browser change blanks only Downloads on screen. */
  view = $state.raw<FilesView | null>(null)
  filesError = $state<string | null>(null)
  loadingFiles = $state(false)
  /* The Files count blanks with a failed load, until a row arrives again. */
  filesBlanked = $state(true)
  flows = $state.raw<FlowsListing | null>(null)
  flowsError = $state<string | null>(null)
  /* The Flows count blanks with a failed files load, until flows answer again. */
  flowsBlanked = $state(false)
  siteData = $state.raw<SiteDataPayload | null>(null)
  siteDataError = $state<string | null>(null)
  history = $state.raw<HistoryPayload | null>(null)
  historyError = $state<string | null>(null)
  /* The workspace's config; null until its first load answers. */
  config = $state.raw<ConfigPayload | null>(null)
  configError = $state<string | null>(null)
  /* The session the session tabs show; the default when none is named. */
  session = $state(DEFAULT_SESSION)
  /* The picked sub-session's own row (`GET …/session?session=`); null for the default. */
  sessionRow = $state.raw<WorkspaceRow | null>(null)
  /* Each sub-session's downloads by name; the default's are `files.downloads`. */
  subDownloads = $state.raw<Record<string, FileEntry[]>>({})
  flowDoc = $state.raw<FlowDoc | null>(null)
  flowDocError = $state<{ name: string; message: string } | null>(null)

  #shownFiles: string | null = null
  #shownBrowser: string | null = null
  #shownLive: boolean | null = null
  #shownFlows: string | number | null = null
  // undefined until a row is seen, so the first row always loads.
  #shownSiteData: string | null | undefined = undefined
  #shownHistory: string | null | undefined = undefined
  #siteLoads = new Latest()
  #historyLoads = new Latest()
  #fileLoads = new Latest()
  #flowLoads = new Latest()
  #docLoads = new Latest()
  #configLoads = new Latest()
  #sessionLoads = new Latest()
  #subLoads = new Latest()
  /* The sessions' liveness and browsers as last loaded for: a change reloads
     Files, since each session's downloads are its browser's. */
  #shownSessions: string | null = null
  // undefined until a row is seen.
  #shownConfig: string | undefined = undefined
  #disposed = false
  readonly key: string
  #api: Api

  constructor(key: string, api: Api, session: string = DEFAULT_SESSION) {
    this.key = key
    this.#api = api
    this.row = { key }
    this.session = session
  }

  /* The session tabs follow the chips: Site data and the Session row load for
     the one picked. Files, Flows, History and Config never do. */
  pick(name: string) {
    if (name === this.session) return
    this.session = name
    this.sessionRow = null
    this.siteData = null
    this.siteDataError = null
    void this.loadSiteData()
    void this.loadSession()
  }

  loadSession(): Promise<void> {
    if (this.#disposed) return Promise.resolve()
    if (this.session === DEFAULT_SESSION) {
      this.#sessionLoads.abort()
      this.sessionRow = null
      return Promise.resolve()
    }
    const named = this.session
    return this.#sessionLoads.run(
      (signal) => this.#api<WorkspaceRow>(workspacePath(this.key, '/session', named), 'GET', undefined, signal),
      (data) => { this.sessionRow = data },
      () => { this.sessionRow = null },
    )
  }

  #sessionsStamp = (row: WorkspaceRow) =>
    (row.sessions ?? []).map((s) => s.name + ':' + (s.live ? 1 : 0) + ':' + (s.browser ?? '')).join(',')

  #loadSubDownloads(): Promise<void> {
    const names = (this.row.sessions ?? []).filter((s) => !s.default).map((s) => s.name)
    if (!names.length) {
      this.#subLoads.abort()
      this.subDownloads = {}
      return Promise.resolve()
    }
    return this.#subLoads.run(
      async (signal) => {
        const out: Record<string, FileEntry[]> = {}
        await Promise.all(names.map(async (n) => {
          try {
            const d = await this.#api<{ downloads?: FileEntry[] }>(workspacePath(this.key, '/files/downloads', n), 'GET', undefined, signal)
            out[n] = d.downloads || []
          } catch (e) {
            if (signal.aborted) throw e
            out[n] = []
          }
        }))
        return out
      },
      (v) => { this.subDownloads = v },
    )
  }

  loadFiles(): Promise<void> {
    if (this.#disposed) return Promise.resolve()
    // Both clears are off for the life of the request, not only once it fails.
    this.loadingFiles = true
    return this.#fileLoads.run(
      (signal) => this.#api<FilesResponse>(workspacePath(this.key, '/files'), 'GET', undefined, signal),
      (data) => {
        const row = data.workspace || { key: this.key }
        this.#showRow(row)
        this.#shownFiles = filesStamp(row)
        this.#shownBrowser = row.session_id || null
        this.#shownLive = !!row.live
        const files = {
          downloads: data.downloads || [],
          screenshots: data.screenshots || [],
          recordings: data.recordings || [],
          files: data.files || [],
          browser: !!data.browser,
        }
        this.files = files
        this.view = {
          ...files,
          downloadsEmpty: downloadsEmpty(files.browser),
          counts: { downloads: files.downloads.length, screenshots: files.screenshots.length, recordings: files.recordings.length, files: files.files.length },
        }
        this.filesError = null
        this.loadingFiles = false
        this.#shownSessions = this.#sessionsStamp(row)
        void this.#loadSubDownloads()
      },
      (e) => {
        this.files = NO_FILES
        this.filesError = e.message
        this.filesBlanked = true
        this.flowsBlanked = true
        this.loadingFiles = false
        // Site data and History read the workspace store, not the Grid: a
        // Files failure must not leave them on Loading… (Copilot, #51).
        if (this.siteData === null && this.siteDataError === null) void this.loadSiteData()
        if (this.history === null && this.historyError === null) void this.loadHistory()
      },
    )
  }

  async filesSettled(): Promise<void> {
    await this.#fileLoads.settled()
    await this.#subLoads.settled()
  }

  flowsSettled(): Promise<void> {
    return this.#flowLoads.settled()
  }

  loadFlows(currentFlow: () => string | null, vanished: () => void): Promise<void> {
    if (this.#disposed) return Promise.resolve()
    return this.#flowLoads.run(
      (signal) => this.#api<FlowsListing>(workspacePath(this.key, '/flows'), 'GET', undefined, signal),
      (data) => {
        this.flows = data
        this.flowsError = null
        // Today's listing repainted the panel from the document it had, so a
        // failed load's error went, and the reload below says anew.
        this.flowDocError = null
        this.flowsBlanked = false
        this.#shownFlows = data.rev || null
        // The open flow is part of the library this listing replaced, and the
        // one most likely to be out of date.
        const name = currentFlow()
        if (name && !(data.flows || []).some((f) => f.name === name)) {
          this.flowDoc = null
          vanished()
        } else if (name) {
          void this.loadFlow(name)
        }
      },
      (e) => { this.flowsError = e.message },
    )
  }

  loadSiteData(): Promise<void> {
    if (this.#disposed) return Promise.resolve()
    return this.#siteLoads.run(
      (signal) => this.#api<SiteDataPayload>(workspacePath(this.key, '/site-data', this.session), 'GET', undefined, signal),
      (data) => { this.siteData = data; this.siteDataError = null },
      (e) => {
        this.siteDataError = e.message
        // Unshown again, so the next push at the same rev tries once more.
        this.#shownSiteData = undefined
      },
    )
  }

  loadHistory(): Promise<void> {
    if (this.#disposed) return Promise.resolve()
    return this.#historyLoads.run(
      (signal) => this.#api<HistoryPayload>(workspacePath(this.key, '/history'), 'GET', undefined, signal),
      (data) => { this.history = data; this.historyError = null },
      (e) => {
        this.historyError = e.message
        // Unshown again, so the next push at the same rev tries once more.
        this.#shownHistory = undefined
      },
    )
  }

  loadConfig(): Promise<void> {
    if (this.#disposed) return Promise.resolve()
    return this.#configLoads.run(
      (signal) => this.#api<ConfigPayload>(workspacePath(this.key, '/config'), 'GET', undefined, signal),
      (data) => this.showConfig(data),
      (e) => { this.configError = e.message },
    )
  }

  /* What a load or a save answered. */
  showConfig(data: ConfigPayload) {
    this.config = data
    this.configError = null
  }

  loadFlow(name: string): Promise<void> {
    if (this.#disposed) return Promise.resolve()
    return this.#docLoads.run(
      (signal) => this.#api<FlowDoc>(workspacePath(this.key, '/flows/' + encodeURIComponent(name)), 'GET', undefined, signal),
      (doc) => { this.flowDoc = doc; this.flowDocError = null },
      (e) => { this.flowDocError = { name, message: e.message } },
    )
  }

  /* A pushed workspace list, applied to this workspace. */
  onPushed(data: WorkspacesPayload, currentFlow: () => string | null, vanished: () => void, browserChanged: () => void) {
    const row = (data.workspaces || []).find((s) => s.key === this.key)
    // Gone from the store means expired: what is on screen is still a true record.
    if (!row) return
    // A different browser is a different download store, and the Grid deletes
    // it with the browser — so only Downloads is blanked. `live` is checked too:
    // a reap can leave the recorded id unchanged.
    if ((row.session_id || null) !== this.#shownBrowser || !!row.live !== this.#shownLive) {
      browserChanged()
      this.#shownBrowser = row.session_id || null
      this.#shownLive = !!row.live
      this.files = NO_FILES
      // Forces the reload below even at an unchanged stamp.
      this.#shownFiles = null
      if (this.view) this.view = { ...this.view, downloads: [], downloadsEmpty: downloadsEmpty(!!row.session_id) }
    }
    this.#showRow(row)
    // A sub-session's browser came or went: its downloads with it.
    if (this.#sessionsStamp(row) !== this.#shownSessions) this.#shownFiles = null
    if (this.session !== DEFAULT_SESSION) void this.loadSession()
    if (filesStamp(row) !== this.#shownFiles) void this.loadFiles()
    if ((row.flows_rev ?? null) !== this.#shownFlows) void this.loadFlows(currentFlow, vanished)
  }

  #showRow(row: WorkspaceRow) {
    this.row = row
    this.headed = true
    const siteMoved = (row.site_data_rev ?? null) !== this.#shownSiteData
    const historyMoved = (row.history_rev ?? null) !== this.#shownHistory
    this.#shownSiteData = row.site_data_rev ?? null
    this.#shownHistory = row.history_rev ?? null
    // Each tab shows some of the other: History's saved pills are site data,
    // and Site data's rows are in history order (Copilot, #51).
    if (siteMoved || historyMoved) {
      void this.loadSiteData()
      void this.loadHistory()
    }
    this.filesBlanked = false
    // A save, a remove or a hand edit moves it, from this tab or another: the
    // Config tab and its banner reload. Mount loads the first.
    const configMoved = this.#shownConfig !== undefined && row.config_rev !== this.#shownConfig
    this.#shownConfig = row.config_rev
    if (configMoved) void this.loadConfig()
  }

  /* Final: a caller still holding the model after its workspace left loads nothing. */
  dispose() {
    this.#disposed = true
    this.#fileLoads.abort()
    this.#flowLoads.abort()
    this.#siteLoads.abort()
    this.#historyLoads.abort()
    this.#docLoads.abort()
    this.#configLoads.abort()
    this.#sessionLoads.abort()
    this.#subLoads.abort()
  }
}
