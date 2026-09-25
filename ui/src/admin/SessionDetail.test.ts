import { fireEvent, render, screen, within } from '@testing-library/svelte'
import { beforeEach, expect, test, vi } from 'vitest'
import FileGrid from '../lib/FileGrid.svelte'
import Lightbox from '../lib/Lightbox.svelte'
import type { FileEntry } from '../lib/types'
import { deferred, fakeFetch } from '../test/helpers'
import { createApi } from './api'
import { Live } from './live.svelte'
import Modal from './Modal.svelte'
import type { ModalSpec } from './modal'
import SessionDetail from './SessionDetail.svelte'

// Spied, not replaced: each still renders for real. The spies only let the
// refusal test hold a handler past the moment its session left the screen,
// which is the one thing the DOM cannot do once the subtree is gone.
vi.mock('./Modal.svelte', { spy: true })
vi.mock('../lib/Lightbox.svelte', { spy: true })
vi.mock('../lib/FileGrid.svelte', { spy: true })

const shot = (name: string) => ({ name, size: 1, url: '/s/' + name, image: true })
const row = { key: 'k', name: 'mine', live: true, attached: true, session_id: 'b1', files_rev: 1, flows_rev: 1, files_count: 3 }
const FILES = { session: row, downloads: [shot('d.png')], screenshots: [shot('a.png'), shot('b.png')], files: [shot('d.png')], browser: true }
const FLOWS = { enabled: true, flows: [], rev: 1, session: 'k' }
const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })

function setup(routes = {}, props = {}) {
  const net = fakeFetch({ 'GET /admin/sessions/k/files': { body: FILES }, 'GET /admin/sessions/k/flows': { body: FLOWS }, ...routes })
  const live = new Live(api, '')
  const r = render(SessionDetail, { key: 'k', tab: 'files', flow: undefined, api, live, root: '', ...props })
  return { ...r, ...net, live }
}
beforeEach(() => { history.replaceState(null, '', '/#/sessions/k'); vi.stubGlobal('alert', vi.fn()) })

const tick = () => new Promise((r) => setTimeout(r, 0))
const lastProps = <P>(spy: unknown, pick: (p: P) => boolean = () => true): P =>
  (vi.mocked(spy as (...a: unknown[]) => unknown).mock.calls.map((c) => c[1] as P).filter(pick).at(-1))!

test('switch-in: Loading… everywhere, then the three rows with counts (D4, F1, F2, F6)', async () => {
  const { container } = setup()
  expect(within(container.querySelector('#screenshots')!).getByText('Loading…')).toBeInTheDocument()
  expect(within(container.querySelector('#downloads')!).getByText('Loading…')).toBeInTheDocument()
  expect(within(container.querySelector('#kept')!).getByText('Loading…')).toBeInTheDocument()
  expect(within(container.querySelector('#flows')!).getByText('Loading…')).toBeInTheDocument()
  expect(container.querySelector('#detailHeader')).toBeEmptyDOMElement()
  expect(container.querySelector('#screenshotsCount')).toHaveTextContent('')
  expect(container.querySelector('#filesTotal')).toHaveTextContent('')
  await vi.waitFor(() => expect(container.querySelector('#screenshotsCount')).toHaveTextContent('2'))
  expect(container.querySelector('#downloadsCount')).toHaveTextContent('1')
  expect(container.querySelector('#keptCount')).toHaveTextContent('1')
  expect(container.querySelector('#filesTotal')).toHaveTextContent('3')
  expect(container.querySelector('#detailHeader')).toHaveTextContent('mine')
  const toggle = container.querySelector('[aria-controls="screenshotsBody"]')!
  expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await fireEvent.click(toggle)
  expect(container.querySelector('#screenshotsSection')).toHaveAttribute('data-open', 'false')
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
})

test('the three Files sections are Downloads, Screenshots, Files, in that order (F1)', async () => {
  const { container } = setup()
  const sections = [...container.querySelectorAll('#paneFiles section.section')]
  expect(sections.map((s) => s.id)).toEqual(['downloadsSection', 'screenshotsSection', 'keptSection'])
  expect(sections.map((s) => s.querySelector('.title')!.textContent!.slice(1))).toEqual(['Downloads', 'Screenshots', 'Files'])
})

test('Files has no clear of its own (F9)', async () => {
  const { container } = setup()
  await vi.waitFor(() => expect(container.querySelector('#keptCount')).toHaveTextContent('1'))
  const buttons = [...container.querySelectorAll('#keptSection .head button')]
  expect(buttons).toHaveLength(1)
  expect(buttons[0]).toHaveClass('title')
})

test('a failed load shows the error in all three rows, blanks the counts and disarms the clears (F7)', async () => {
  const flows = deferred<{ body: unknown }>()
  const { container } = setup({ 'GET /admin/sessions/k/files': { status: 500, body: { error: 'boom' } }, 'GET /admin/sessions/k/flows': () => flows.promise })
  await vi.waitFor(() => expect(within(container.querySelector('#kept')!).getByText('boom')).toHaveClass('error'))
  expect(within(container.querySelector('#downloads')!).getByText('boom')).toBeInTheDocument()
  expect(within(container.querySelector('#screenshots')!).getByText('boom')).toBeInTheDocument()
  for (const id of ['#downloadsCount', '#screenshotsCount', '#keptCount', '#filesTotal', '#flowsTotal']) {
    expect(container.querySelector(id)).toHaveTextContent('')
  }
  expect(container.querySelector('#clearDownloads')).toBeDisabled()
  expect(container.querySelector('#clearScreenshots')).toBeDisabled()
  // Nothing about this session has answered, so the header stays blank.
  expect(container.querySelector('#detailHeader')).toBeEmptyDOMElement()
  // The Flows count comes back with the flows, as today.
  flows.resolve({ body: FLOWS })
  await vi.waitFor(() => expect(container.querySelector('#flowsTotal')).toHaveTextContent('0'))
})

test('Files and Flows are tabs, routed by the hash (D3)', async () => {
  const { container } = setup()
  await vi.waitFor(() => expect(container.querySelector('#tabFlows')).not.toBeNull())
  expect(container.querySelector('#sessionTabs')).toHaveAttribute('role', 'tablist')
  await fireEvent.click(container.querySelector('#tabFlows')!)
  expect(location.hash).toBe('#/sessions/k/flows')
  expect(container.querySelector('#tabFiles')).toHaveAttribute('aria-selected', 'true') // the prop moves, not the click
})

test('a section collapsed on Files is still collapsed after a look at Flows, with nothing refetched (D3)', async () => {
  const { container, calls, rerender } = setup()
  await vi.waitFor(() => expect(container.querySelector('#flowsTotal')).toHaveTextContent('0'))
  await fireEvent.click(container.querySelector('[aria-controls="screenshotsBody"]')!)
  const before = calls.length
  await rerender({ tab: 'flows' })
  expect(container.querySelector('#paneFiles')).not.toBeVisible()
  expect(container.querySelector('#paneFlows')).toBeVisible()
  expect(container.querySelector('#tabFlows')).toHaveAttribute('aria-selected', 'true')
  await rerender({ tab: 'files' })
  expect(container.querySelector('#paneFiles')).toBeVisible()
  expect(container.querySelector('#screenshotsSection')).toHaveAttribute('data-open', 'false')
  expect(calls.length).toBe(before)
})

test('← Sessions goes to the list (R6)', async () => {
  const { container } = setup()
  await fireEvent.click(container.querySelector('#back')!)
  expect(location.hash).toBe('#/')
})

test('Clear downloads needs a live browser and this session’s files (F3)', async () => {
  const { container } = setup({ 'GET /admin/sessions/k/files': { body: { ...FILES, session: { ...row, live: false } } } })
  await vi.waitFor(() => expect(container.querySelector('#downloadsCount')).toHaveTextContent('1'))
  expect(container.querySelector('#clearDownloads')).toBeDisabled()
})

test('both clears disarm the moment a files load goes out, before it answers (F3, F4)', async () => {
  const slow = deferred<{ body: unknown }>()
  let n = 0
  const { container, live } = setup({ 'GET /admin/sessions/k/files': () => (++n === 1 ? { body: FILES } : slow.promise) })
  await vi.waitFor(() => expect(container.querySelector('#clearDownloads')).not.toBeDisabled())
  expect(container.querySelector('#clearScreenshots')).not.toBeDisabled()
  live.data = { sessions: [{ ...row, files_rev: 2 }] }
  await vi.waitFor(() => expect(container.querySelector('#clearDownloads')).toBeDisabled())
  expect(container.querySelector('#clearScreenshots')).toBeDisabled()
  expect(n).toBe(2) // out, and not answered
  slow.resolve({ body: FILES })
  await vi.waitFor(() => expect(container.querySelector('#clearDownloads')).not.toBeDisabled())
  expect(container.querySelector('#clearScreenshots')).not.toBeDisabled()
})

test('Clear downloads lists every name with its fate, and clears (F3)', async () => {
  const { container, calls } = setup({
    'GET /admin/sessions/k/files': { body: { ...FILES, downloads: [shot('d.png'), shot('e.pdf')] } },
    'DELETE /admin/sessions/k/files/downloads': { body: {} },
  })
  await vi.waitFor(() => expect(container.querySelector('#clearDownloads')).not.toBeDisabled())
  await fireEvent.click(container.querySelector('#clearDownloads')!)
  const sheet = container.ownerDocument.querySelector('.modal') as HTMLElement
  expect(within(sheet).getByText('Clear downloads')).toHaveClass('head')
  expect(sheet).toHaveTextContent('Deletes what this browser downloaded. The Grid has no per-file delete, so this clears all of them.')
  const names = [...sheet.querySelectorAll('.names li')]
  expect(names.map((li) => li.textContent)).toEqual(['d.png — copy in Files stays', 'e.pdf — gone'])
  expect(sheet.querySelector('.fate.stays')).toHaveTextContent('— copy in Files stays')
  expect(within(sheet).getByText('Clear 2 files')).toHaveClass('danger')
  await fireEvent.click(within(sheet).getByText('Clear 2 files'))
  await vi.waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.path === '/admin/sessions/k/files/downloads')).toBe(true))
  await vi.waitFor(() => expect(calls.filter((c) => c.path === '/admin/sessions/k/files')).toHaveLength(2))
  await vi.waitFor(() => expect(container.ownerDocument.querySelector('.modal')).toBeNull())
})

test('Clear downloads with nothing downloaded says so, and reads Clear (F3)', async () => {
  const { container } = setup({ 'GET /admin/sessions/k/files': { body: { ...FILES, downloads: [] } } })
  await vi.waitFor(() => expect(container.querySelector('#clearDownloads')).not.toBeDisabled())
  await fireEvent.click(container.querySelector('#clearDownloads')!)
  const sheet = container.ownerDocument.querySelector('.modal') as HTMLElement
  expect(sheet).toHaveTextContent('There is nothing downloaded to clear.')
  expect(within(sheet).getByText('Clear')).toHaveClass('danger')
})

test('Clear screenshots lists them and says what stays (F4)', async () => {
  const { container, calls } = setup({ 'DELETE /admin/sessions/k/files/screenshots': { body: {} } })
  await vi.waitFor(() => expect(container.querySelector('#clearScreenshots')).not.toBeDisabled())
  await fireEvent.click(container.querySelector('#clearScreenshots')!)
  const sheet = container.ownerDocument.querySelector('.modal') as HTMLElement
  expect(sheet).toHaveTextContent('Deletes all 2 screenshots in this session. Anything you kept is in Files and stays.')
  expect([...sheet.querySelectorAll('.names li')].map((li) => li.textContent)).toEqual(['a.png', 'b.png'])
  expect(within(sheet).getByText('Delete 2 screenshots')).toHaveClass('danger')
  await fireEvent.click(within(sheet).getByText('Delete 2 screenshots'))
  await vi.waitFor(() => expect(calls.filter((c) => c.path === '/admin/sessions/k/files')).toHaveLength(2))
  expect(calls.some((c) => c.method === 'DELETE' && c.path === '/admin/sessions/k/files/screenshots')).toBe(true)
})

test('clearing the one and only screenshot says "the 1 screenshot", not "all 1" (F4)', async () => {
  const { container } = setup({ 'GET /admin/sessions/k/files': { body: { ...FILES, screenshots: [shot('a.png')] } } })
  await vi.waitFor(() => expect(container.querySelector('#clearScreenshots')).not.toBeDisabled())
  await fireEvent.click(container.querySelector('#clearScreenshots')!)
  const sheet = container.ownerDocument.querySelector('.modal') as HTMLElement
  expect(sheet).toHaveTextContent('Deletes the 1 screenshot in this session. Anything you kept is in Files and stays.')
  expect(within(sheet).getByText('Delete 1 screenshot')).toBeInTheDocument()
})

test('Clear screenshots is off when there are none (F4)', async () => {
  const { container } = setup({ 'GET /admin/sessions/k/files': { body: { ...FILES, screenshots: [] } } })
  await vi.waitFor(() => expect(container.querySelector('#screenshotsCount')).toHaveTextContent('0'))
  expect(container.querySelector('#clearScreenshots')).toBeDisabled()
  expect(within(container.querySelector('#screenshots')!).getByText('No screenshots yet.')).toBeInTheDocument()
})

test('a tile keep posts to its own folder and reloads; a failure alerts (F5)', async () => {
  const { container, calls } = setup({ 'POST /admin/sessions/k/files/screenshots/a.png/keep': { status: 409, body: { error: 'clash' } } })
  await vi.waitFor(() => expect(container.querySelector('#screenshots button.keep')).not.toBeNull())
  await fireEvent.click(container.querySelector('#screenshots button.keep')!)
  await vi.waitFor(() => expect(alert).toHaveBeenCalledWith('Could not keep that file: clash'))
  expect(calls.at(-1)!.path).toBe('/admin/sessions/k/files/screenshots/a.png/keep')
})

test('a successful tile keep of a download reloads the files (F5)', async () => {
  const { container, calls } = setup({ 'POST /admin/sessions/k/files/downloads/d.png/keep': { body: {} } })
  await vi.waitFor(() => expect(container.querySelector('#downloads button.keep')).not.toBeNull())
  await fireEvent.click(container.querySelector('#downloads button.keep')!)
  await vi.waitFor(() => expect(calls.filter((c) => c.path === '/admin/sessions/k/files')).toHaveLength(2))
  expect(calls.some((c) => c.method === 'POST' && c.path === '/admin/sessions/k/files/downloads/d.png/keep')).toBe(true)
  expect(alert).not.toHaveBeenCalled()
})

test('a Files tile deletes behind a confirm (F5)', async () => {
  const { container, calls } = setup({ 'DELETE /admin/sessions/k/files/d.png': { body: {} } })
  await vi.waitFor(() => expect(container.querySelector('#kept button.drop')).not.toBeNull())
  await fireEvent.click(container.querySelector('#kept button.drop')!)
  const sheet = container.ownerDocument.querySelector('.modal') as HTMLElement
  expect(within(sheet).getByText('Delete a file')).toHaveClass('head')
  expect(sheet).toHaveTextContent('This removes it from Files for good. There is no undo.')
  expect(sheet.querySelector('.names li')).toHaveTextContent('d.png')
  await fireEvent.click(within(sheet).getByText('Delete'))
  await vi.waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.path === '/admin/sessions/k/files/d.png')).toBe(true))
  await vi.waitFor(() => expect(calls.filter((c) => c.path === '/admin/sessions/k/files')).toHaveLength(2))
})

test('the lightbox Keep moves on to the next screenshot (X3)', async () => {
  let kept = false
  const { container } = setup({
    'GET /admin/sessions/k/files': () => ({ body: kept ? { ...FILES, screenshots: [shot('b.png')], files_rev: 2 } : FILES }),
    'POST /admin/sessions/k/files/screenshots/a.png/keep': () => { kept = true; return { body: {} } },
  })
  await vi.waitFor(() => expect(container.querySelector('#screenshots a.thumb')).not.toBeNull())
  await fireEvent.click(container.querySelector('#screenshots a.thumb')!)
  expect(screen.getByText('1 / 2')).toBeInTheDocument()
  await fireEvent.click(screen.getByText('📌 Keep'))
  await vi.waitFor(() => expect(screen.getByText('1 / 1')).toBeInTheDocument())
  expect(container.ownerDocument.querySelector('.lightbox .name')).toHaveTextContent('b.png')
})

test('the lightbox Delete on Files asks first, and a cancel is silent (X1, X3)', async () => {
  const { container, calls } = setup()
  await vi.waitFor(() => expect(container.querySelector('#kept a.thumb')).not.toBeNull())
  await fireEvent.click(container.querySelector('#kept a.thumb')!)
  await fireEvent.click(screen.getByText('🗑 Delete'))
  const sheet = container.ownerDocument.querySelector('.modal') as HTMLElement
  expect(sheet).toHaveTextContent('This removes it from Files for good. There is no undo.')
  await fireEvent.click(within(sheet).getByText('Cancel'))
  await vi.waitFor(() => expect(screen.getByText('🗑 Delete')).not.toBeDisabled())
  expect(alert).not.toHaveBeenCalled()
  expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
})

test('End browser asks, deletes, and reloads; disabled when not attached (D2)', async () => {
  vi.stubGlobal('confirm', vi.fn(() => true))
  const { container, calls } = setup({ 'DELETE /admin/sessions/k': { body: {} } })
  expect(container.querySelector('#endBrowser')).toBeDisabled()
  await vi.waitFor(() => expect(container.querySelector('#endBrowser')).not.toBeDisabled())
  await fireEvent.click(container.querySelector('#endBrowser')!)
  expect(confirm).toHaveBeenCalledWith('End this browser? The session and its context are kept — whatever the browser was holding is lost.')
  await vi.waitFor(() => expect(calls.filter((c) => c.path === '/admin/sessions/k/files').length).toBe(2))
  await vi.waitFor(() => expect(calls.filter((c) => c.path === '/admin/sessions/k/flows').length).toBe(2))
})

test('End browser: a refusal alerts, a no at the confirm sends nothing, and it is off unattached (D2)', async () => {
  vi.stubGlobal('confirm', vi.fn(() => false))
  const { container, calls } = setup({ 'DELETE /admin/sessions/k': { status: 500, body: { error: 'grid down' } } })
  await vi.waitFor(() => expect(container.querySelector('#endBrowser')).not.toBeDisabled())
  await fireEvent.click(container.querySelector('#endBrowser')!)
  expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
  vi.stubGlobal('confirm', vi.fn(() => true))
  await fireEvent.click(container.querySelector('#endBrowser')!)
  await vi.waitFor(() => expect(alert).toHaveBeenCalledWith('Could not end the browser: grid down'))
  const off = setup({ 'GET /admin/sessions/k/files': { body: { ...FILES, session: { ...row, attached: false, live: false } } } })
  await vi.waitFor(() => expect(off.container.querySelector('#downloadsCount')).toHaveTextContent('1'))
  expect(off.container.querySelector('#endBrowser')).toBeDisabled()
})

test('End browser on a session since left reloads the list instead (D2)', async () => {
  vi.stubGlobal('confirm', vi.fn(() => true))
  const ended = deferred<{ body: unknown }>()
  const { container, calls, unmount } = setup({
    'DELETE /admin/sessions/k': () => ended.promise,
    'GET /admin/sessions': { body: { sessions: [] } },
  })
  await vi.waitFor(() => expect(container.querySelector('#endBrowser')).not.toBeDisabled())
  await fireEvent.click(container.querySelector('#endBrowser')!)
  unmount()
  const before = calls.filter((c) => c.path === '/admin/sessions/k/files').length
  ended.resolve({ body: {} })
  await vi.waitFor(() => expect(calls.some((c) => c.path === '/admin/sessions')).toBe(true))
  expect(calls.filter((c) => c.path === '/admin/sessions/k/files').length).toBe(before)
})

test('ending the browser closes its Downloads lightbox and confirm, and reloads (D2)', async () => {
  vi.stubGlobal('confirm', vi.fn(() => true))
  const { container, calls } = setup({ 'DELETE /admin/sessions/k': { body: {} } })
  await vi.waitFor(() => expect(container.querySelector('#clearDownloads')).not.toBeDisabled())
  await fireEvent.click(container.querySelector('#downloads a.thumb')!)
  await fireEvent.click(container.querySelector('#clearDownloads')!)
  expect(document.querySelector('.lightbox')).not.toBeNull()
  expect(document.querySelector('.modal')).not.toBeNull()
  await fireEvent.click(container.querySelector('#endBrowser')!)
  await vi.waitFor(() => expect(calls.filter((c) => c.path === '/admin/sessions/k/files')).toHaveLength(2))
  expect(document.querySelector('.lightbox')).toBeNull()
  expect(document.querySelector('.modal')).toBeNull()
})

test('a browser change closes the Downloads lightbox and its confirm (F8)', async () => {
  const { container, live } = setup()
  await vi.waitFor(() => expect(container.querySelector('#downloads a.thumb')).not.toBeNull())
  await fireEvent.click(container.querySelector('#downloads a.thumb')!)
  expect(container.ownerDocument.querySelector('.lightbox')).not.toBeNull()
  live.data = { sessions: [{ ...row, session_id: 'b2' }] }
  await vi.waitFor(() => expect(container.ownerDocument.querySelector('.lightbox')).toBeNull())

  await vi.waitFor(() => expect(container.querySelector('#clearDownloads')).not.toBeDisabled())
  await fireEvent.click(container.querySelector('#clearDownloads')!)
  expect(container.ownerDocument.querySelector('.modal')).not.toBeNull()
  live.data = { sessions: [{ ...row, session_id: 'b3' }] }
  await vi.waitFor(() => expect(container.ownerDocument.querySelector('.modal')).toBeNull())
})

test('a browser change leaves a Screenshots lightbox and a session-scoped confirm open (F8)', async () => {
  const { container, calls, live } = setup()
  await vi.waitFor(() => expect(container.querySelector('#clearScreenshots')).not.toBeDisabled())
  await fireEvent.click(container.querySelector('#screenshots a.thumb')!)
  await fireEvent.click(container.querySelector('#clearScreenshots')!)
  live.data = { sessions: [{ ...row, session_id: 'b2' }] }
  await vi.waitFor(() => expect(calls.filter((c) => c.path === '/admin/sessions/k/files')).toHaveLength(2))
  expect(container.ownerDocument.querySelector('.lightbox')).not.toBeNull()
  expect(container.ownerDocument.querySelector('.modal')).not.toBeNull()
})

test('the session going away takes its overlays and cancels its modal (D4, M1)', async () => {
  const { container, unmount } = setup()
  await vi.waitFor(() => expect(container.querySelector('#clearScreenshots')).not.toBeDisabled())
  await fireEvent.click(container.querySelector('#screenshots a.thumb')!)
  await fireEvent.click(container.querySelector('#clearScreenshots')!)
  unmount()
  expect(document.querySelector('.modal')).toBeNull()
  expect(document.querySelector('.lightbox')).toBeNull()
})

const B_ROW = { key: 'b', name: 'other', live: true, attached: true, session_id: 'c1', files_rev: 1, flows_rev: 1, files_count: 1 }
const B_FILES = { session: B_ROW, downloads: [], screenshots: [shot('z.png')], files: [], browser: true }
const B_ROUTES = {
  'GET /admin/sessions/b/files': { body: B_FILES },
  'GET /admin/sessions/b/flows': { body: { ...FLOWS, session: 'b' } },
}

/* Session A's request is still out when the operator moves to B. It answers
   late: A must not reload (it is not on screen), and B keeps exactly what its
   own load produced. */
async function leaveThenLand(calls: { method: string; path: string }[], unmount: () => void, land: () => void) {
  unmount()
  const b = render(SessionDetail, { key: 'b', tab: 'files', flow: undefined, api, live: new Live(api, ''), root: '' })
  await vi.waitFor(() => expect(calls.some((c) => c.path === '/admin/sessions/b/flows')).toBe(true))
  await vi.waitFor(() => expect(b.container.querySelector('#screenshotsCount')).toHaveTextContent('1'))
  const before = calls.length
  land()
  await tick()
  expect(calls.slice(before)).toEqual([])
  expect(b.container.querySelector('#screenshotsCount')).toHaveTextContent('1')
  expect(b.container.querySelector('#downloadsCount')).toHaveTextContent('0')
  expect(b.container.querySelector('#screenshots .name')).toHaveTextContent('z.png')
  expect(b.container.querySelector('#filesTotal')).toHaveTextContent('1')
}

test('a file action (keep, delete) from a session that has since been left does not paint over the session now on screen (D4)', async () => {
  const kept = deferred<{ body: unknown }>()
  const dropped = deferred<{ body: unknown }>()
  const { container, calls, unmount } = setup({
    'POST /admin/sessions/k/files/screenshots/a.png/keep': () => kept.promise,
    'DELETE /admin/sessions/k/files/d.png': () => dropped.promise,
    ...B_ROUTES,
  })
  await vi.waitFor(() => expect(container.querySelector('#kept button.drop')).not.toBeNull())
  await fireEvent.click(container.querySelector('#screenshots button.keep')!)
  await fireEvent.click(container.querySelector('#kept button.drop')!)
  await fireEvent.click(within(document.querySelector('.modal') as HTMLElement).getByText('Delete'))
  await vi.waitFor(() => expect(calls.filter((c) => c.method !== 'GET')).toHaveLength(2))
  await leaveThenLand(calls, unmount, () => { kept.resolve({ body: {} }); dropped.resolve({ body: {} }) })
})

test('a file action (clear) from a session that has since been left does not paint over the session now on screen (D4)', async () => {
  const shots = deferred<{ body: unknown }>()
  const downloads = deferred<{ body: unknown }>()
  const { container, calls, unmount } = setup({
    'DELETE /admin/sessions/k/files/screenshots': () => shots.promise,
    'DELETE /admin/sessions/k/files/downloads': () => downloads.promise,
    ...B_ROUTES,
  })
  await vi.waitFor(() => expect(container.querySelector('#clearDownloads')).not.toBeDisabled())
  await fireEvent.click(container.querySelector('#clearScreenshots')!)
  await fireEvent.click(within(document.querySelector('.modal') as HTMLElement).getByText('Delete 2 screenshots'))
  await fireEvent.click(container.querySelector('#clearDownloads')!)
  await fireEvent.click(within(document.querySelector('.modal') as HTMLElement).getByText('Clear 1 file'))
  await vi.waitFor(() => expect(calls.filter((c) => c.method === 'DELETE')).toHaveLength(2))
  await leaveThenLand(calls, unmount, () => { shots.resolve({ body: {} }); downloads.resolve({ body: {} }) })
})

test('a keep, delete or clear — from a tile, a confirm or the lightbox — that lands after the session was left is refused, not run or dropped (D4)', async () => {
  const { container, calls, unmount } = setup()
  await vi.waitFor(() => expect(container.querySelector('#clearDownloads')).not.toBeDisabled())
  const a = shot('a.png') as FileEntry
  const d = shot('d.png') as FileEntry

  // Every handler below is taken while the session is on screen and run after it left.
  const tile = lastProps<{ empty: string; onkeep: (f: FileEntry) => Promise<boolean> }>(FileGrid, (p) => p.empty === 'No screenshots yet.')
  const onkeep = tile.onkeep
  const specs: ModalSpec<never>[] = []
  for (const open of ['#clearScreenshots', '#clearDownloads', '#kept button.drop']) {
    await fireEvent.click(container.querySelector(open)!)
    specs.push(lastProps<{ spec: ModalSpec<never> }>(Modal).spec)
    await fireEvent.click(within(document.querySelector('.modal') as HTMLElement).getByText('Cancel'))
  }
  const actions = []
  for (const thumb of ['#screenshots a.thumb', '#kept a.thumb']) {
    await fireEvent.click(container.querySelector(thumb)!)
    actions.push(lastProps<{ action: { run: (f: FileEntry) => Promise<unknown> } }>(Lightbox).action)
    await fireEvent.click(screen.getByText('Close'))
  }
  const before = calls.length
  unmount()

  expect(await onkeep(a)).toBe(false)
  expect(alert).toHaveBeenCalledWith('Could not keep that file: that session is no longer on screen')
  for (const spec of specs) await expect(spec.onconfirm()).rejects.toThrow('that session is no longer on screen')
  await expect(actions[0].run(a)).rejects.toThrow('that session is no longer on screen')
  await expect(actions[1].run(d)).rejects.toThrow('that session is no longer on screen')
  await tick()
  expect(calls.slice(before)).toEqual([])
})
