import { vi } from 'vitest'
import type { Channel } from '../admin/liveview.svelte'

export class FakeSocket extends EventTarget {
  static all: FakeSocket[] = []
  binaryType = 'blob'
  protocol = ''
  readyState = 1
  sent: unknown[] = []
  closed: [number | undefined, string | undefined] | null = null
  constructor(public url: string) { super(); FakeSocket.all.push(this) }
  send(d: unknown) { this.sent.push(d) }
  close(code?: number, reason?: string) { this.closed = [code, reason]; this.readyState = 3 }
  say(data: string | ArrayBuffer) { this.dispatchEvent(new MessageEvent('message', { data })) }
  shut(code: number, reason: string) { this.readyState = 3; this.dispatchEvent(new CloseEvent('close', { code, reason })) }
}

export class FakeRfb extends EventTarget {
  static all: FakeRfb[] = []
  viewOnly = false
  background = ''
  scaleViewport = false
  resizeSession = true
  disconnected = false
  sendKey = vi.fn()
  constructor(public target: HTMLElement, public channel: Channel, public options: object) { super(); FakeRfb.all.push(this) }
  disconnect() { this.disconnected = true }
}

export const MINT = { url: '/admin/workspaces/k/live/socket?exp=1&sig=s&by=b', heartbeat: 10, grace: 30 }
