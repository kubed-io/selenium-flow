import type { Api } from './api'
import { sessionPath } from './api'
import { Latest } from './latest'
import type { FileEntry, FilesData, FilesResponse, FlowDoc, FlowsListing, SessionRow, SessionsPayload } from '../lib/types'

/* Handed to a session that has not answered yet, or whose load failed: the
   clears must never act on a previous session's names. */
export const NO_FILES: FilesData = Object.freeze({ downloads: [], screenshots: [], files: [], browser: false }) as FilesData

/* Not a count: deleting the kept copy of a name that is also a download leaves
   the count alone while the tile changes. The browser id is in it because a
   different browser is a different file store. */
export const filesStamp = (row: SessionRow) => (row.session_id || '-') + ':' + row.files_rev

const downloadsEmpty = (hasBrowser: boolean) => (hasBrowser ? 'No downloads.' : 'No browser — downloads go with it.')

export interface FilesView {
  downloads: FileEntry[]
  screenshots: FileEntry[]
  files: FileEntry[]
  downloadsEmpty: string
  /* The pills, set only by a load that answers — as today, a browser change
     blanks the Downloads grid but leaves its pill until the reload lands. */
  counts: { downloads: number; screenshots: number; files: number }
}

/* One session on screen. Created by SessionDetail and disposed with it, so
   nothing it loads can land on another session's page. */
export class SessionModel {
  // Raw state throughout: every value is replaced wholesale, and `files` is
  // compared by identity with NO_FILES — a deep proxy would never be equal.
  row = $state.raw<SessionRow>({ key: '' })
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
  flowDoc = $state.raw<FlowDoc | null>(null)
  flowDocError = $state<{ name: string; message: string } | null>(null)

  #shownFiles: string | null = null
  #shownBrowser: string | null = null
  #shownLive: boolean | null = null
  #shownFlows: string | number | null = null
  #fileLoads = new Latest()
  #flowLoads = new Latest()
  #docLoads = new Latest()

  constructor(readonly key: string, private api: Api) {
    this.row = { key }
  }

  loadFiles(): Promise<void> {
    // Both clears are off for the life of the request, not only once it fails.
    this.loadingFiles = true
    return this.#fileLoads.run(
      (signal) => this.api<FilesResponse>(sessionPath(this.key, '/files'), 'GET', undefined, signal),
      (data) => {
        const row = data.session || { key: this.key }
        this.#showRow(row)
        this.#shownFiles = filesStamp(row)
        this.#shownBrowser = row.session_id || null
        this.#shownLive = !!row.live
        const files = {
          downloads: data.downloads || [],
          screenshots: data.screenshots || [],
          files: data.files || [],
          browser: !!data.browser,
        }
        this.files = files
        this.view = {
          ...files,
          downloadsEmpty: downloadsEmpty(files.browser),
          counts: { downloads: files.downloads.length, screenshots: files.screenshots.length, files: files.files.length },
        }
        this.filesError = null
        this.loadingFiles = false
      },
      (e) => {
        this.files = NO_FILES
        this.filesError = e.message
        this.filesBlanked = true
        this.flowsBlanked = true
        this.loadingFiles = false
      },
    )
  }

  filesSettled(): Promise<void> {
    return this.#fileLoads.settled()
  }

  loadFlows(currentFlow: () => string | null, vanished: () => void): Promise<void> {
    return this.#flowLoads.run(
      (signal) => this.api<FlowsListing>(sessionPath(this.key, '/flows'), 'GET', undefined, signal),
      (data) => {
        this.flows = data
        this.flowsError = null
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

  loadFlow(name: string): Promise<void> {
    return this.#docLoads.run(
      (signal) => this.api<FlowDoc>(sessionPath(this.key, '/flows/' + encodeURIComponent(name)), 'GET', undefined, signal),
      (doc) => { this.flowDoc = doc; this.flowDocError = null },
      (e) => { this.flowDocError = { name, message: e.message } },
    )
  }

  /* A pushed session list, applied to this session. */
  onPushed(data: SessionsPayload, currentFlow: () => string | null, vanished: () => void, browserChanged: () => void) {
    const row = (data.sessions || []).find((s) => s.key === this.key)
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
    if (filesStamp(row) !== this.#shownFiles) void this.loadFiles()
    if ((row.flows_rev ?? null) !== this.#shownFlows) void this.loadFlows(currentFlow, vanished)
  }

  #showRow(row: SessionRow) {
    this.row = row
    this.headed = true
    this.filesBlanked = false
  }

  dispose() {
    this.#fileLoads.abort()
    this.#flowLoads.abort()
    this.#docLoads.abort()
  }
}
