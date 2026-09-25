import { expect, test, vi } from 'vitest'
import { deferred, fakeFetch } from '../test/helpers'
import { createApi } from './api'
import { NO_FILES, SessionModel } from './session.svelte'

const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })
const shot = (name: string) => ({ name, size: 1, url: '/' + name, image: true })
const files = (over = {}) => ({ session: { key: 'k', live: true, session_id: 'b1', files_rev: 1, flows_rev: 1 }, downloads: [], screenshots: [shot('a.png')], files: [], browser: true, ...over })

test('a load paints the rows and the header (F2, D1)', async () => {
  fakeFetch({ 'GET /admin/sessions/k/files': { body: files() } })
  const m = new SessionModel('k', api)
  expect(m.view).toBeNull()
  expect(m.files).toBe(NO_FILES) // nothing for the clears to act on yet
  await m.loadFiles()
  expect(m.view!.screenshots.map((f) => f.name)).toEqual(['a.png'])
  expect(m.row.session_id).toBe('b1')
  expect(m.files).not.toBe(NO_FILES)
})

test('a failed load leaves nothing actionable behind (F7)', async () => {
  fakeFetch({ 'GET /admin/sessions/k/files': { status: 500, body: { error: 'boom' } } })
  const m = new SessionModel('k', api)
  await m.loadFiles()
  expect(m.files).toBe(NO_FILES)
  expect(m.filesError).toBe('boom')
  expect(m.flowsBlanked).toBe(true)
})

test('dispose: an answer after the session left never paints (D4)', async () => {
  const late = deferred<{ body: unknown }>()
  fakeFetch({ 'GET /admin/sessions/k/files': () => late.promise })
  const m = new SessionModel('k', api)
  const load = m.loadFiles()
  m.dispose()
  late.resolve({ body: files() }); await load
  expect(m.view).toBeNull()
})

test('filesSettled sees the newest data even when the poll overtook the caller (X3, the Keep race)', async () => {
  const keeps = deferred<{ body: unknown }>()
  const polls = deferred<{ body: unknown }>()
  let n = 0
  fakeFetch({ 'GET /admin/sessions/k/files': () => (++n === 1 ? keeps.promise : polls.promise) })
  const m = new SessionModel('k', api)
  // The lightbox's refresh after a Keep: its own reload, then the newest.
  const refreshed = (async () => { await m.loadFiles(); await m.filesSettled(); return m.files })()
  void m.loadFiles()                                   // the poll's, overtaking it
  keeps.resolve({ body: files() })                     // Keep's own load stands down unpainted
  await new Promise((r) => setTimeout(r, 0))
  polls.resolve({ body: files({ screenshots: [shot('b.png')] }) })
  expect((await refreshed).screenshots.map((f) => f.name)).toEqual(['b.png'])
})

test('a pushed row refetches files only when the stamp moves (F8)', async () => {
  const { calls } = fakeFetch({ 'GET /admin/sessions/k/files': { body: files() }, 'GET /admin/sessions/k/flows': { body: { enabled: true, flows: [], rev: 1 } } })
  const m = new SessionModel('k', api)
  await m.loadFiles(); await m.loadFlows(() => null, () => {})
  const before = calls.length
  m.onPushed({ sessions: [{ key: 'k', live: true, session_id: 'b1', files_rev: 1, flows_rev: 1 }] }, () => null, () => {}, () => {})
  expect(calls.length).toBe(before)
  m.onPushed({ sessions: [{ key: 'k', live: true, session_id: 'b1', files_rev: 2, flows_rev: 1 }] }, () => null, () => {}, () => {})
  expect(calls.length).toBe(before + 1)
})

test('a pushed row refetches flows only when flows_rev moves (F8, W7)', async () => {
  const { calls } = fakeFetch({ 'GET /admin/sessions/k/files': { body: files() }, 'GET /admin/sessions/k/flows': { body: { enabled: true, flows: [], rev: 1 } } })
  const m = new SessionModel('k', api)
  await m.loadFiles(); await m.loadFlows(() => null, () => {})
  const flowLoads = () => calls.filter((c) => c.path === '/admin/sessions/k/flows').length
  const fileLoads = () => calls.filter((c) => c.path === '/admin/sessions/k/files').length
  m.onPushed({ sessions: [{ key: 'k', live: true, session_id: 'b1', files_rev: 1, flows_rev: 1 }] }, () => null, () => {}, () => {})
  expect(flowLoads()).toBe(1)
  m.onPushed({ sessions: [{ key: 'k', live: true, session_id: 'b1', files_rev: 1, flows_rev: 2 }] }, () => null, () => {}, () => {})
  expect(flowLoads()).toBe(2)
  expect(fileLoads()).toBe(1) // the files stamp did not move
})

test('a changed browser blanks Downloads, disarms the clears, and forces a reload (F8)', async () => {
  fakeFetch({ 'GET /admin/sessions/k/files': { body: files({ downloads: [shot('d.pdf')] }) }, 'GET /admin/sessions/k/flows': { body: { enabled: true, flows: [], rev: 1 } } })
  const m = new SessionModel('k', api)
  await m.loadFiles(); await m.loadFlows(() => null, () => {})
  const changed = vi.fn()
  const load = vi.spyOn(m, 'loadFiles')
  m.onPushed({ sessions: [{ key: 'k', live: false, session_id: 'b1', files_rev: 1, flows_rev: 1 }] }, () => null, () => {}, changed)
  expect(changed).toHaveBeenCalledOnce()
  expect(m.files).toBe(NO_FILES)
  expect(m.view!.downloads).toEqual([])
  expect(m.view!.downloadsEmpty).toBe('No downloads.')
  expect(m.view!.screenshots).toHaveLength(1) // session-owned rows are untouched
  expect(m.view!.counts.downloads).toBe(1) // as today, the pill waits for the reload
  expect(load).toHaveBeenCalled()
})

test('a pushed row for another session, or none, changes nothing', () => {
  fakeFetch({})
  const m = new SessionModel('k', api)
  m.onPushed({ sessions: [{ key: 'other' }] }, () => null, () => {}, () => {})
  expect(m.row).toEqual({ key: 'k' })
})

test('an open flow that vanished from the listing is closed (W7)', async () => {
  fakeFetch({ 'GET /admin/sessions/k/flows': { body: { enabled: true, flows: [{ name: 'b', step_count: 1 }], rev: 2 } } })
  const m = new SessionModel('k', api)
  const vanished = vi.fn()
  await m.loadFlows(() => 'a', vanished)
  expect(vanished).toHaveBeenCalledOnce()
  expect(m.flowDoc).toBeNull()
})

test('a still-listed open flow reloads its document (W7)', async () => {
  fakeFetch({
    'GET /admin/sessions/k/flows': { body: { enabled: true, flows: [{ name: 'a', step_count: 1 }], rev: 2 } },
    'GET /admin/sessions/k/flows/a': { body: { name: 'a', steps: [] } },
  })
  const m = new SessionModel('k', api)
  await m.loadFlows(() => 'a', () => {})
  await vi.waitFor(() => expect(m.flowDoc?.name).toBe('a'))
})
