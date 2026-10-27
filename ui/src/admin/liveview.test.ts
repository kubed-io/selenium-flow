import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ApiError, type Api } from './api'
import { FakeRfb, FakeSocket } from '../test/live'
import { Channel, LiveView, PASTE_CAP, liveUnavailable, socketUrl } from './liveview.svelte'

const MINT = { url: '/flow/admin/workspaces/k/live/socket?exp=1&sig=s&by=b', heartbeat: 10, grace: 30 }
const mkApi = () => vi.fn(async (path: string) => (path.endsWith('/paste') ? { typed: 3 } : { ...MINT })) as unknown as Api & ReturnType<typeof vi.fn>

function make(api: Api = mkApi()) {
  const target = document.createElement('div')
  const view = new LiveView({
    api, key: 'k', root: '/flow', target,
    load: async () => ({ RFB: FakeRfb as never }),
    WebSocket: FakeSocket as never,
  })
  return { view, api: api as ReturnType<typeof vi.fn>, target }
}
const visible = (v: 'visible' | 'hidden') => {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => v })
  document.dispatchEvent(new Event('visibilitychange'))
}
const sock = () => FakeSocket.all.at(-1)!
const rfb = () => FakeRfb.all.at(-1)!

beforeEach(() => { FakeSocket.all = []; FakeRfb.all = [] })
afterEach(() => { vi.useRealTimers(); visible('visible') })

test('keeps text frames from noVNC and passes it only ArrayBuffers', () => {
  const s = new FakeSocket('x')
  const texts: string[] = []
  const ch = new Channel(s as never, (t) => texts.push(t))
  const got: unknown[] = []
  ch.onmessage = (e) => got.push((e as MessageEvent).data)
  s.say('{"type":"mode","mode":"watch"}')
  const buf = new ArrayBuffer(4)
  s.say(buf)
  expect(texts).toEqual(['{"type":"mode","mode":"watch"}'])
  expect(got).toEqual([buf])
  expect(s.binaryType).toBe('arraybuffer')
})

test('forwards open, error and close to the handlers noVNC sets', () => {
  const s = new FakeSocket('x')
  const ch = new Channel(s as never, () => {})
  const seen: string[] = []
  ch.onopen = () => seen.push('open')
  ch.onerror = () => seen.push('error')
  ch.onclose = () => seen.push('close')
  s.dispatchEvent(new Event('open')); s.dispatchEvent(new Event('error')); s.shut(1000, '')
  expect(seen).toEqual(['open', 'error', 'close'])
})

test('has every property Websock.attach checks, as own keys or on its prototype', () => {
  const ch = new Channel(new FakeSocket('x') as never, () => {})
  const have = [...Object.keys(ch), ...Object.getOwnPropertyNames(Object.getPrototypeOf(ch))]
  for (const p of ['send', 'close', 'binaryType', 'onerror', 'onmessage', 'onopen', 'protocol', 'readyState']) expect(have).toContain(p)
  ch.binaryType = 'arraybuffer'
  expect(ch.binaryType).toBe('arraybuffer')
  expect(ch.readyState).toBe(1)
  ch.send(new Uint8Array([1]))
})

test('starts in watch, viewOnly, scaled, never resizing the session', async () => {
  const { view, target } = make()
  await view.connect()
  expect(view.mode).toBe('watch')
  expect(view.state).toBe('connecting')
  expect(sock().url).toMatch(/^ws:\/\/[^/]+\/flow\/flow\/admin\/workspaces\/k\/live\/socket\?exp=1&sig=s&by=b$/)
  expect(rfb().target).toBe(target)
  expect(rfb().options).toEqual({ shared: true })
  expect(rfb()).toMatchObject({ viewOnly: true, scaleViewport: true, resizeSession: false })
  rfb().dispatchEvent(new Event('connect'))
  expect(view.state).toBe('connected')
  view.dispose()
})

test('moves the toggle only when the proxy answers', async () => {
  const { view } = make()
  await view.connect()
  view.ask('control')
  expect(sock().sent).toEqual(['{"type":"mode","mode":"control"}'])
  expect(view.mode).toBe('watch')
  expect(rfb().viewOnly).toBe(true)
  sock().say('{"type":"mode","mode":"control"}')
  expect(view.mode).toBe('control')
  expect(rfb().viewOnly).toBe(false)
  sock().say('{"type":"mode","mode":"watch"}')
  expect(view.mode).toBe('watch')
  expect(rfb().viewOnly).toBe(true)
  view.dispose()
})

test('sends presence every heartbeat while visible, none while hidden, one at once on becoming visible', async () => {
  vi.useFakeTimers()
  const { view } = make()
  await view.connect()
  const presence = () => sock().sent.filter((m) => m === '{"type":"presence"}').length
  vi.advanceTimersByTime(10_000)
  expect(presence()).toBe(1)
  visible('hidden')
  vi.advanceTimersByTime(30_000)
  expect(presence()).toBe(1)
  visible('visible')
  expect(presence()).toBe(2)
  vi.advanceTimersByTime(10_000)
  expect(presence()).toBe(3)
  view.dispose()
  vi.advanceTimersByTime(60_000)
  expect(presence()).toBe(3)
  visible('hidden'); visible('visible')
  expect(presence()).toBe(3)
})

test('dispose closes the socket and noVNC', async () => {
  const { view } = make()
  await view.connect()
  view.dispose()
  expect(sock().closed?.[0]).toBe(1000)
  expect(rfb().disconnected).toBe(true)
})

test('is disconnected with the close reason and does not reconnect by itself', async () => {
  vi.useFakeTimers()
  const { view, api } = make()
  await view.connect()
  sock().shut(4001, 'idle for too long')
  expect(view.state).toBe('disconnected')
  expect(view.reason).toBe('idle for too long')
  vi.advanceTimersByTime(120_000)
  expect(FakeSocket.all).toHaveLength(1)
  expect(api).toHaveBeenCalledTimes(1)
  view.dispose()
})

test('falls back to a sentence when the close has no reason', async () => {
  const { view } = make()
  await view.connect()
  sock().shut(1006, '')
  expect(view.reason).toBe('the live view closed')
  view.dispose()
})

test('mints a fresh link on reconnect', async () => {
  const { view, api } = make()
  await view.connect()
  sock().shut(1006, '')
  await view.connect()
  expect(api).toHaveBeenCalledTimes(2)
  expect(FakeSocket.all).toHaveLength(2)
  expect(FakeRfb.all).toHaveLength(2)
  expect(view.state).toBe('connecting')
  expect(view.mode).toBe('watch')
  expect(api.mock.calls[0][0]).toBe('/admin/workspaces/k/live')
  view.dispose()
})

test('says so when the link cannot be minted, and when the viewer cannot load', async () => {
  const bad = vi.fn(async () => { throw new ApiError('no browser', 404) }) as unknown as Api
  const a = make(bad)
  await a.view.connect()
  expect(a.view.state).toBe('disconnected')
  expect(a.view.reason).toBe('no browser')
  const target = document.createElement('div')
  const view = new LiveView({ api: mkApi(), key: 'k', root: '', target, load: async () => { throw new Error('404') }, WebSocket: FakeSocket as never })
  await view.connect()
  expect(view.unavailable).toBe('no live view · this page could not load the viewer')
  expect(FakeSocket.all).toHaveLength(0)
})

test('sends no paste in watch, and posts it in control', async () => {
  const { view, api } = make()
  await view.connect()
  await expect(view.paste('x')).rejects.toThrow()
  expect(api).toHaveBeenCalledTimes(1)
  sock().say('{"type":"mode","mode":"control"}')
  const before = sock().sent.length
  await expect(view.paste('héllo – 日本 ✓')).resolves.toBe(3)
  expect(api).toHaveBeenLastCalledWith('/admin/workspaces/k/live/paste', 'POST', { text: 'héllo – 日本 ✓' })
  expect(sock().sent).toHaveLength(before)
  expect(rfb().sendKey).not.toHaveBeenCalled()
  view.dispose()
})

test('caps a paste at 10 000 characters', async () => {
  const { view, api } = make()
  await view.connect()
  sock().say('{"type":"mode","mode":"control"}')
  await view.paste('日'.repeat(PASTE_CAP + 5))
  expect(api.mock.calls.at(-1)![2].text).toHaveLength(PASTE_CAP)
  view.dispose()
})

test('hands back a refused paste as the server said it', async () => {
  const api = vi.fn(async (path: string) => {
    if (path.endsWith('/paste')) throw new ApiError('not in control', 409)
    return { ...MINT }
  }) as unknown as Api
  const { view } = make(api)
  await view.connect()
  sock().say('{"type":"mode","mode":"control"}')
  await expect(view.paste('x')).rejects.toThrow('not in control')
  view.dispose()
})

test('says why there is no live view', () => {
  const idle = { key: 'k', live: false }
  const novnc = { key: 'k', live: true, ran: { version: null, node: null, live_view: false } }
  const ok = { key: 'k', live: true, ran: { version: null, node: null, live_view: true } }
  expect(liveUnavailable(ok, true)).toBeNull()
  expect(liveUnavailable(ok, undefined)).toBeNull()
  expect(liveUnavailable(ok, false)).toBe('no live view · switched off')
  expect(liveUnavailable(idle, false)).toBe('no live view · switched off')
  expect(liveUnavailable(idle, true)).toBe('no live view · no browser')
  expect(liveUnavailable(novnc, true)).toBe('no live view · this Grid has no VNC')
  expect(liveUnavailable({ key: 'k', live: true }, true)).toBe('no live view · this Grid has no VNC')
})

test('builds ws: and wss: URLs from the page', () => {
  const at = (protocol: string, host: string) => ({ protocol, host }) as Location
  expect(socketUrl('/flow', '/flow/admin/x?a=1', at('https:', 'h.example'))).toBe('wss://h.example/flow/flow/admin/x?a=1')
  expect(socketUrl('', '/admin/x', at('http:', 'localhost:8000'))).toBe('ws://localhost:8000/admin/x')
})

test('a drop returns to watch', async () => {
  const { view } = make()
  await view.connect()
  sock().say('{"type":"mode","mode":"control"}')
  sock().shut(1006, '')
  expect(view.mode).toBe('watch')
  view.dispose()
})

test('reconnecting does not disconnect an RFB noVNC already ended', async () => {
  const { view } = make()
  await view.connect()
  const first = rfb()
  first.dispatchEvent(new Event('disconnect'))
  sock().shut(1006, '')
  await view.connect()
  expect(first.disconnected).toBe(false)
  view.dispose()
})
