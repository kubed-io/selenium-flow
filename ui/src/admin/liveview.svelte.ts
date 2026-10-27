import type { RFB } from '../live'
import type { WorkspaceRow } from '../lib/types'
import { workspacePath, type Api } from './api'

export type LiveState = 'connecting' | 'connected' | 'disconnected'
export type LiveMode = 'watch' | 'control'
export interface LiveMint { url: string; heartbeat: number; grace: number }
interface LiveOptions {
  api: Api
  key: string
  root: string
  target: HTMLElement
  load: () => Promise<{ RFB: RfbClass }>
  WebSocket?: typeof WebSocket
}
type RfbClass = new (target: HTMLElement, channel: object, options?: { shared?: boolean }) => RFB

export const PASTE_CAP = 10_000
const CLOSED = 'the live view closed'
const NO_SCRIPT = 'no live view · this page could not load the viewer'

/* Why the tab is greyed, or null. Hover text, in the order a person would fix them. */
export function liveUnavailable(row: WorkspaceRow, liveEnabled: boolean | undefined): string | null {
  if (liveEnabled === false) return 'no live view · switched off'
  if (!row.live) return 'no live view · no browser'
  if (row.ran?.live_view !== true) return 'no live view · this Grid has no VNC'
  return null
}

/* The minted path is the server's, mount included; ROOT is what an ingress stripped. */
export function socketUrl(root: string, url: string, here: Pick<Location, 'protocol' | 'host'> = location): string {
  return (here.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + here.host + root + url
}

/* What noVNC attaches to. Its Websock.attach looks, by name, at this object's own
   keys and its immediate prototype only, so the handlers are own fields and the
   rest sit on the class. Text frames are the proxy's control messages and never
   reach noVNC, which is handed ArrayBuffers alone. */
export class Channel {
  onerror: ((e: Event) => void) | null = null
  onmessage: ((e: MessageEvent) => void) | null = null
  onopen: ((e: Event) => void) | null = null
  onclose: ((e: CloseEvent) => void) | null = null
  readonly #socket: WebSocket

  constructor(socket: WebSocket, ontext: (text: string) => void) {
    this.#socket = socket
    socket.binaryType = 'arraybuffer'
    socket.addEventListener('open', (e) => this.onopen?.(e))
    socket.addEventListener('error', (e) => this.onerror?.(e))
    socket.addEventListener('close', (e) => this.onclose?.(e as CloseEvent))
    socket.addEventListener('message', (e) => {
      if (typeof e.data === 'string') ontext(e.data)
      else if (e.data instanceof ArrayBuffer) this.onmessage?.(e)
    })
  }

  send(data: ArrayBufferLike | ArrayBufferView | string): void { this.#socket.send(data as string) }
  close(code?: number, reason?: string): void { this.#socket.close(code, reason) }
  get binaryType(): BinaryType { return this.#socket.binaryType }
  set binaryType(v: BinaryType) { this.#socket.binaryType = v }
  get protocol(): string { return this.#socket.protocol }
  get readyState(): number { return this.#socket.readyState }
}

/* One person's look at one workspace's browser: the signed socket, noVNC on a
   Channel over it, presence while the tab shows, and the mode the server says. */
export class LiveView {
  state = $state<LiveState>('connecting')
  mode = $state<LiveMode>('watch')
  reason = $state('')
  /* Why there is no viewer at all, when the script would not load. */
  unavailable = $state('')
  readonly #opts: LiveOptions
  #socket: WebSocket | null = null
  #rfb: RFB | null = null
  #beat: ReturnType<typeof setInterval> | undefined
  #seen: (() => void) | null = null
  #gone = false

  constructor(opts: LiveOptions) {
    this.#opts = opts
  }

  async connect(): Promise<void> {
    this.#teardown()
    this.state = 'connecting'
    this.mode = 'watch'
    this.reason = ''
    const { api, key, root, target, load } = this.#opts
    this.unavailable = ''
    let mint: LiveMint
    try {
      mint = await api<LiveMint>(workspacePath(key, '/live'))
    } catch (err) {
      this.#fail((err as Error).message)
      return
    }
    let Rfb: RfbClass
    try {
      Rfb = (await load()).RFB
    } catch {
      if (!this.#gone) this.unavailable = NO_SCRIPT
      return
    }
    if (this.#gone) return
    const Socket = this.#opts.WebSocket ?? WebSocket
    const socket = new Socket(socketUrl(root, mint.url))
    this.#socket = socket
    socket.addEventListener('close', (e) => this.#socket === socket && this.#fail((e as CloseEvent).reason || CLOSED))
    const rfb = new Rfb(target, new Channel(socket, (t) => this.#text(t)), { shared: true })
    rfb.scaleViewport = true
    rfb.resizeSession = false
    rfb.viewOnly = true
    rfb.background = 'transparent'
    rfb.addEventListener('connect', () => { this.state = 'connected' })
    // noVNC has moved to disconnected itself: another disconnect() only logs an error.
    rfb.addEventListener('disconnect', () => { if (this.#rfb === rfb) this.#rfb = null })
    this.#rfb = rfb

    const presence = () => {
      if (document.visibilityState === 'visible' && socket.readyState === 1) socket.send('{"type":"presence"}')
    }
    this.#beat = setInterval(presence, mint.heartbeat * 1000)
    this.#seen = () => { if (document.visibilityState === 'visible') presence() }
    document.addEventListener('visibilitychange', this.#seen)
  }

  ask(mode: LiveMode): void {
    if (this.#socket?.readyState === 1) this.#socket.send(JSON.stringify({ type: 'mode', mode }))
  }

  async paste(text: string): Promise<number> {
    if (this.mode !== 'control') throw new Error('Paste types into the browser in Control')
    const chars = text.length > PASTE_CAP ? [...text].slice(0, PASTE_CAP).join('') : text
    const done = await this.#opts.api<{ typed: number }>(workspacePath(this.#opts.key, '/live/paste'), 'POST', { text: chars })
    return done.typed
  }

  dispose(): void {
    this.#gone = true
    this.#teardown()
  }

  #text(raw: string) {
    let frame: { type?: string; mode?: string }
    try { frame = JSON.parse(raw) } catch { return }
    if (frame.type !== 'mode' || (frame.mode !== 'watch' && frame.mode !== 'control')) return
    this.mode = frame.mode
    if (this.#rfb) this.#rfb.viewOnly = frame.mode === 'watch'
  }

  #fail(reason: string) {
    if (this.#gone) return
    this.state = 'disconnected'
    this.mode = 'watch'
    this.reason = reason
    clearInterval(this.#beat)
  }

  #teardown() {
    clearInterval(this.#beat)
    this.#beat = undefined
    if (this.#seen) document.removeEventListener('visibilitychange', this.#seen)
    this.#seen = null
    this.#rfb?.disconnect()
    this.#rfb = null
    const socket = this.#socket
    this.#socket = null
    // A closed socket's own close event must not read as a drop.
    if (socket && socket.readyState < 2) socket.close(1000)
  }
}
