import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { deferred, FakeEventSource, fakeFetch } from '../test/helpers'
import { createApi } from './api'
import { Live } from './live.svelte'

const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })
beforeEach(() => { vi.useFakeTimers(); vi.stubGlobal('EventSource', FakeEventSource) })
afterEach(() => { vi.useRealTimers() })

test('loads, then streams from the signed URL against ROOT (U1, U2)', async () => {
  fakeFetch({ 'GET /admin/sessions': { body: { sessions: [{ key: 'a' }], events_url: '/flow/admin/events?sig=1' } } })
  const live = new Live(api, '/base')
  expect(live.badge).toEqual({ text: 'connecting…', cls: '' })
  await live.load()
  expect(live.data?.sessions).toEqual([{ key: 'a' }])
  expect(FakeEventSource.last!.url).toBe('/base/flow/admin/events?sig=1')
  FakeEventSource.last!.onopen!()
  expect(live.badge).toEqual({ text: 'live', cls: 'live' })
  FakeEventSource.last!.emit({ sessions: [{ key: 'b' }] })
  expect(live.data?.sessions).toEqual([{ key: 'b' }])
  FakeEventSource.last!.onerror!()
  expect(live.badge.text).toBe('reconnecting…')
})

test('a silent stream falls back to polling and reopens on a fresh URL (U3)', async () => {
  let n = 0
  fakeFetch({ 'GET /admin/sessions': () => ({ body: { sessions: [], events_url: `/e?sig=${++n}` } }) })
  const live = new Live(api, '')
  await live.load()
  const first = FakeEventSource.last!
  first.onopen!()
  await vi.advanceTimersByTimeAsync(30_000)
  expect(FakeEventSource.last).toBe(first) // heard from within 45 s
  await vi.advanceTimersByTimeAsync(30_000)
  expect(live.badge.text).toBe('polling')
  expect(first.closed).toBe(true)
  expect(FakeEventSource.last!.url).toBe('/e?sig=2')
})

test('an error shows in place of the list (L2)', async () => {
  fakeFetch({ 'GET /admin/sessions': { status: 500, body: { error: 'grid down' } } })
  const live = new Live(api, '')
  await live.load()
  expect(live.error).toBe('grid down')
})

test('stop closes the stream and the poll (A4)', async () => {
  fakeFetch({ 'GET /admin/sessions': { body: { sessions: [], events_url: '/e' } } })
  const live = new Live(api, '')
  await live.load()
  live.stop()
  expect(FakeEventSource.last!.closed).toBe(true)
  expect(live.watching).toBe(false)
})

test('stop() invalidates a load() already in flight (Copilot, #42)', async () => {
  const gate = deferred<{ status?: number; body?: unknown }>()
  fakeFetch({ 'GET /admin/sessions': () => gate.promise })
  const live = new Live(api, '')
  const before = FakeEventSource.last

  const pending = live.load()
  live.stop()
  gate.resolve({ body: { sessions: [{ key: 'a' }], events_url: '/e' } })
  await pending

  // A stale response must neither paint nor reopen a stream that stop()
  // already tore down.
  expect(FakeEventSource.last).toBe(before)
  expect(live.data).toBeNull()
})
