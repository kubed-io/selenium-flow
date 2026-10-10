import { isError, isFailed } from '../lib/capture'
import type { CaptureOff, ConsoleData, ConsoleEntry, NetworkData, RequestRow } from '../lib/types'
import { workspacePath, type Api } from './api'

export const TAIL_ROWS = 2000
type Row = ConsoleEntry | RequestRow

const level = (r: Row) => ('level' in r ? r.level : '')
const KEEP: Record<string, (r: Row) => boolean> = {
  all: () => true,
  errors: (r) => isError(r as ConsoleEntry),
  warnings: (r) => level(r) === 'warn' || level(r) === 'warning',
  xhr: (r) => ['xhr', 'fetch'].includes((r as RequestRow).type),
  failed: (r) => isFailed(r as RequestRow),
}
const num = (n: number) => n.toLocaleString('en-US')

/* One page's console or network, as Captured (read from the server) or Live
   (a stream). Rows are text for text nodes; nothing here builds a URL but the
   stream's, from the server-minted `tail_url`. */
export class CaptureModel {
  mode = $state<'captured' | 'live'>('captured')
  paused = $state(false)
  filter = $state('all')
  text = $state('')
  pages = $state(1)
  clearedAt = $state<number | null>(null)
  rows = $state.raw<Row[]>([])
  /* What Pause froze on screen; rows keep arriving behind it. */
  held = $state.raw<Row[] | null>(null)
  seen = $state(0)
  fresh = $state(0)
  dropped = $state(0)
  data = $state.raw<ConsoleData | NetworkData | CaptureOff | null>(null)
  error = $state<string | null>(null)
  #stream: EventSource | null = null
  readonly #api: Api
  readonly #root: string
  readonly #key: string
  readonly kind: 'console' | 'network'

  constructor(api: Api, root: string, key: string, kind: 'console' | 'network') {
    this.#api = api
    this.#root = root
    this.#key = key
    this.kind = kind
  }

  readonly visible = $derived.by(() => {
    const keep = KEEP[this.filter] ?? KEEP.all
    const q = this.text.trim().toLowerCase()
    const cut = this.clearedAt
    const text = (r: Row) => ('text' in r ? r.text : r.url).toLowerCase()
    return (this.paused ? this.held ?? this.rows : this.rows)
      .filter((r) => keep(r) && (!q || text(r).includes(q)) && (cut === null || r.at > cut))
  })

  readonly counts = $derived.by(() => {
    const out: Record<string, number> = {}
    for (const k of this.kind === 'console' ? ['all', 'errors', 'warnings'] : ['all', 'xhr', 'failed']) {
      out[k] = this.rows.filter(KEEP[k]).length
    }
    return out
  })

  /* "Live · 12 messages", "Paused · 12 messages · 3 new", or the Captured line. */
  readonly footer = $derived.by(() => {
    const noun = this.kind === 'console' ? 'messages' : 'requests'
    const uri = 'workspace://' + this.kind
    if (this.mode === 'live') {
      return `${this.paused ? 'Paused' : 'Live'} · ${num(this.seen)} ${noun}` + (this.paused ? ` · ${num(this.fresh)} new` : '')
    }
    const shown = num(this.visible.length)
    if (this.filter !== 'all' || this.text.trim()) return `${shown} of ${num(this.rows.length)} ${noun} · ${uri}`
    const d = this.data && 'not_kept' in this.data ? this.data.not_kept : null
    const n = d ? ('debug' in d ? d.debug : d.static) : 0
    return `${shown} ${noun}` + (n ? ` · ${num(n)} ${this.kind === 'console' ? 'debug' : 'static'} not kept` : '') + ` · ${uri}`
  })

  async load(): Promise<void> {
    try {
      const d = await this.#api<ConsoleData | NetworkData | CaptureOff>(workspacePath(this.#key, `/${this.kind}?pages=${this.pages}`))
      this.data = d
      // Live rows are not the load's to replace; it only refreshes the link.
      if (this.mode === 'captured') {
        const list = 'entries' in d ? d.entries : 'requests' in d ? d.requests : []
        this.rows = [...list].sort((a, b) => b.seq - a.seq)
      }
    } catch (err) {
      this.error = (err as Error).message
    }
  }

  live(): void {
    const link = this.data && 'tail_url' in this.data ? this.data.tail_url : undefined
    if (!link) { this.error = 'Live is not available for this workspace.'; return }
    this.dispose()
    this.error = null
    this.mode = 'live'
    this.rows = []
    this.held = null
    this.paused = false
    this.seen = this.fresh = this.dropped = 0
    const es = new EventSource(this.#root + link + (link.includes('?') ? '&' : '?') + 'kind=' + this.kind)
    es.onmessage = (e) => {
      let row: Row & { dropped?: number }
      try { row = JSON.parse(e.data) } catch { return }
      if (typeof row.dropped === 'number') { this.dropped = row.dropped; return }
      if (this.paused) this.fresh++
      else this.seen++
      this.rows = [row, ...this.rows].slice(0, TAIL_ROWS)
    }
    es.onerror = () => {
      if (es.readyState !== 2) return
      this.error = 'The live stream ended.'
      // The signed link may have expired: fetch a fresh one for the next Live.
      void this.load()
    }
    this.#stream = es
  }

  async captured(): Promise<void> {
    this.dispose()
    this.mode = 'captured'
    this.paused = false
    this.held = null
    this.error = null
    await this.load()
  }

  pause(): void {
    this.held = this.rows
    this.paused = true
  }

  resume(): void {
    this.paused = false
    this.held = null
    this.seen += this.fresh
    this.fresh = 0
  }

  clear(): void {
    this.clearedAt = Date.now() / 1000
  }

  dispose(): void {
    this.#stream?.close()
    this.#stream = null
  }
}
