import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { FakeEventSource, networkData, req, stubEventSource } from '../test/capture'
import type { RequestRow } from '../lib/types'
import { CaptureModel, TAIL_ROWS } from './capture.svelte'

const api = vi.fn()
beforeEach(() => { api.mockReset(); stubEventSource() })
afterEach(() => vi.unstubAllGlobals())

test('live keeps the last 2 000 rows, pause counts new ones, drops show only when non-zero', () => {
  const m = new CaptureModel(api, '', 'ws', 'network')
  m.data = networkData
  m.live()
  const es = FakeEventSource.last
  expect(es.url).toBe('/admin/workspaces/ws/tail?exp=1&sig=x&kind=network')
  for (let i = 0; i < 2005; i++) es.emit({ id: `1.${i}`, url: `https://a.test/${i}`, kept: true })
  expect(m.rows).toHaveLength(TAIL_ROWS)
  expect(m.seen).toBe(2005)
  m.pause()
  es.emit({ id: '1.9999', url: 'https://a.test/late', kept: true })
  expect(m.fresh).toBe(1)
  expect((m.visible[0] as RequestRow).id).toBe('1.2004')
  es.emit({ dropped: 12 })
  expect(m.dropped).toBe(12)
  m.resume()
  expect((m.visible[0] as RequestRow).id).toBe('1.9999')
  expect(m.fresh).toBe(0)
})

test('the stream closes on every way out', async () => {
  api.mockResolvedValue(networkData)
  const m = new CaptureModel(api, '', 'ws', 'network')
  await m.load()
  m.live()
  const first = FakeEventSource.last
  await m.captured()
  expect(first.closed).toBe(true)
  m.live()
  m.dispose()
  expect(FakeEventSource.last.closed).toBe(true)
})

test('an & is used when the link has no query yet, and a stream is not opened for a capture-off answer', () => {
  const m = new CaptureModel(api, '/p', 'ws', 'console')
  m.data = { capture: false, how: 'x' }
  m.live()
  expect(m.error).toBeTruthy()
  m.data = { ...networkData, tail_url: '/t' }
  m.live()
  expect(FakeEventSource.last.url).toBe('/p/t?kind=console')
})

test('the chips count the captured rows, and the box filters them', async () => {
  api.mockResolvedValue(networkData)
  const m = new CaptureModel(api, '', 'ws', 'network')
  await m.load()
  expect(api).toHaveBeenCalledWith('/admin/workspaces/ws/network?pages=1')
  expect(m.counts).toEqual({ all: 5, xhr: 3, failed: 2 })
  m.filter = 'failed'
  expect(m.visible.map((r) => (r as RequestRow).id)).toEqual(['1.5', '1.2'])
  m.filter = 'all'
  m.text = 'nothing'
  expect(m.visible).toHaveLength(0)
  expect(req({}).id).toBe('1.1')
})
