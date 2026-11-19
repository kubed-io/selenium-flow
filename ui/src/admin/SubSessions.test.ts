import { fireEvent, render, screen, within } from '@testing-library/svelte'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { fakeFetch } from '../test/helpers'
import { FakeRfb, FakeSocket, MINT } from '../test/live'
import { networkData, req, stubEventSource } from '../test/capture'
import { createApi } from './api'
import { resetFolds } from './folds.svelte'
import { Live } from './live.svelte'
import WorkspaceDetail from './WorkspaceDetail.svelte'
import { parse } from './router.svelte'

/* Sub-sessions (UI-64, UI-65): the split workspace page. */
const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })
beforeEach(() => { history.replaceState(null, '', '/#/workspaces/k'); vi.stubGlobal('alert', vi.fn()) })
afterEach(() => resetFolds())

const DEFAULT = { name: 'default', label: 'default', default: true, live: true, browser: 'firefox' }
const TESTER = { name: 'tester', label: 'tester', default: false, live: true, browser: 'chrome' }
const ALEX = { name: 'alex', label: 'alex', default: false, live: false, browser: null }
const sub = (n: number) => ({ name: 's' + n, label: 's' + n, default: false, live: false, browser: null })

const rowWith = (sessions: unknown[]) => ({
  key: 'k', name: 'mine', kept: true, live: true, attached: true, session_id: 'b1', files_rev: 1, flows_rev: 1,
  files_count: 3, history_count: 4, history_rev: 'h1', capture: true, captured: { console: 1, network: 2, errors: 0, failed_requests: 0 },
  sessions,
})
const TESTER_ROW = { key: 'k', session: 'tester', label: 'tester', default: false, live: true, attached: true, session_id: 'b2', browser: 'chrome', capture: true, captured: { console: 7, network: 9, errors: 0, failed_requests: 0 } }
const file = (name: string) => ({ name, size: 1, url: '/d/' + name, image: false })

const now = Date.now() / 1000
const HISTORY = {
  key: 'k', clears: [],
  sites: [
    { site: 'a.example.test', url: 'https://a.example.test/', at: now - 60, saved: null, secrets: [], session: 'tester' },
    { site: 'b.example.test', url: 'https://b.example.test/', at: now - 120, saved: null, secrets: [], session: 'default' },
    { site: 'c.example.test', url: 'https://c.example.test/', at: now - 180, saved: null, secrets: [], session: 'default' },
    { site: 'd.example.test', url: 'https://d.example.test/', at: now - 240, saved: null, secrets: [], session: 'alex' },
  ],
}

function setup(sessions: unknown[], props: Record<string, unknown> = {}, routes: Record<string, unknown> = {}) {
  const row = rowWith(sessions)
  const net = fakeFetch({
    'GET /admin/workspaces/k/files': { body: { workspace: row, downloads: [file('d.pdf')], screenshots: [], recordings: [], files: [], browser: true } },
    'GET /admin/workspaces/k/files/downloads': { body: { key: 'k', session: 'tester', browser: true, downloads: [file('t.pdf')] } },
    'GET /admin/workspaces/k/flows': { body: { enabled: true, flows: [], rev: 1, workspace: 'k' } },
    'GET /admin/workspaces/k/site-data': { body: { key: 'k', saved_at: 0, uri: '', sites: [], details: {} } },
    'GET /admin/workspaces/k/history': { body: HISTORY },
    'GET /admin/workspaces/k/session': { body: TESTER_ROW },
    'GET /admin/workspaces/k/console': { body: { capture: true, pages: [], entries: [], count: 0, total: 0, not_kept: { debug: 0 } } },
    'DELETE /admin/workspaces/k/session': { body: { success: true, key: 'k', session_id: 'b2' } },
    'POST /admin/workspaces/k/end-all': { body: { success: true, key: 'k', ended: 2 } },
    ...routes,
  } as never)
  const live = new Live(api, '')
  const r = render(WorkspaceDetail, { key: 'k', tab: 'session', flow: undefined, api, live, root: '', liveScript: '', ...props })
  return { ...r, ...net, live }
}
const loaded = (c: HTMLElement) => vi.waitFor(() => expect(c.querySelector('#detailHeader')).toHaveTextContent('mine'))

test('the tabs: Session, Live, Console, Network, Site data, a divider, Files, Flows, History, Config (UI-64)', async () => {
  const { container } = setup([DEFAULT])
  const bar = container.querySelector('#workspaceTabs')!
  const seen = [...bar.children].map((e) => (e.matches('button') ? e.firstChild!.textContent!.trim() : '|'))
  expect(seen).toEqual(['Session', 'Live', 'Console', 'Network', 'Site data', '|', 'Files', 'Flows', 'History', 'Config'])
  expect(bar.querySelector('.divider')).toHaveAttribute('aria-hidden', 'true')
})

test('no chips with only the default session: End browser stays in the toolbar', async () => {
  const { container } = setup([DEFAULT])
  await loaded(container)
  expect(screen.queryByRole('radiogroup')).toBeNull()
  expect(screen.queryByText('Sub-sessions')).toBeNull()
  expect(screen.queryByRole('button', { name: 'End all' })).toBeNull()
  expect(container.querySelector('#endBrowser')).toHaveTextContent('End browser')
  expect(container.querySelector('#sessionArea')).not.toHaveClass('sub')
})

test('chips: default first, a divider, Sub-sessions, then each by its label; the dot says live', async () => {
  const { container } = setup([DEFAULT, TESTER, ALEX])
  await loaded(container)
  const group = screen.getByRole('radiogroup', { name: 'Session' })
  const chips = within(group).getAllByRole('radio')
  expect(chips.map((c) => c.textContent!.replace(/\s+/g, ' ').trim())).toEqual(['default firefox', 'tester chrome', 'alex'])
  expect(chips[0]).toHaveAttribute('aria-checked', 'true')
  expect(within(chips[1]).getByRole('img', { name: 'live' })).toBeInTheDocument()
  expect(within(chips[2]).getByRole('img', { name: 'not live' })).toBeInTheDocument()
  expect(within(group).getByText('Sub-sessions')).toBeInTheDocument()
  expect(group.querySelector('.divider')).not.toBeNull()
})

test('past five sub-sessions the rest go in a +N menu', async () => {
  const { container } = setup([DEFAULT, sub(1), sub(2), sub(3), sub(4), sub(5), sub(6), sub(7)])
  await loaded(container)
  const group = screen.getByRole('radiogroup', { name: 'Session' })
  expect(within(group).getAllByRole('radio')).toHaveLength(6)
  const more = screen.getByRole('button', { name: '+2' })
  expect(more).toHaveAttribute('aria-expanded', 'false')
  await fireEvent.click(more)
  const items = screen.getAllByRole('menuitem')
  expect(items.map((i) => i.textContent!.trim())).toEqual(['s6', 's7'])
  await fireEvent.click(items[1])
  expect(location.hash).toBe('#/workspaces/k?session=s7')
})

test('exactly five sub-sessions need no menu', async () => {
  const { container } = setup([DEFAULT, sub(1), sub(2), sub(3), sub(4), sub(5)])
  await loaded(container)
  expect(screen.queryByRole('button', { name: /^\+/ })).toBeNull()
})

test('chips are a radio group with arrow keys', async () => {
  const { container } = setup([DEFAULT, TESTER, ALEX])
  await loaded(container)
  const [d, t] = screen.getAllByRole('radio')
  expect(d).toHaveAttribute('tabindex', '0')
  expect(t).toHaveAttribute('tabindex', '-1')
  await fireEvent.keyDown(d, { key: 'ArrowRight' })
  expect(location.hash).toBe('#/workspaces/k?session=tester')
})

test('picking default drops the session from the address', async () => {
  history.replaceState(null, '', '/#/workspaces/k/console?session=tester')
  const { container } = setup([DEFAULT, TESTER], { tab: 'console', session: 'tester' })
  await loaded(container)
  await fireEvent.click(screen.getByRole('radio', { name: /default/ }))
  expect(location.hash).toBe('#/workspaces/k/console')
})

test('the picked session is in the address: a reload or a shared link keeps it', () => {
  expect(parse('#/workspaces/k?session=tester')).toMatchObject({ key: 'k', tab: 'session', session: 'tester' })
  expect(parse('#/workspaces/k/console?session=a%20b')).toMatchObject({ tab: 'console', session: 'a b' })
  expect(parse('#/workspaces/k/flows/f1?session=tester')).toMatchObject({ tab: 'flows', flow: 'f1', session: 'tester' })
  expect(parse('#/workspaces/k/console')).not.toHaveProperty('session', 'tester')
  expect((parse('#/workspaces/k/console') as { session?: string }).session).toBeUndefined()
})

test('the Session tab names its session', async () => {
  const { container } = setup([DEFAULT, TESTER], { session: 'tester' })
  await loaded(container)
  await vi.waitFor(() => expect(container.querySelector('#workspaceCard')).toHaveTextContent(/session · tester/i))
})

test('the default session card reads SESSION · DEFAULT when there are sub-sessions', async () => {
  const { container } = setup([DEFAULT, TESTER])
  await loaded(container)
  expect(container.querySelector('#workspaceCard .label')).toHaveTextContent('session · default')
})

test('End tester names the picked session and ends only it', async () => {
  vi.stubGlobal('confirm', vi.fn(() => true))
  const { container, calls } = setup([DEFAULT, TESTER], { session: 'tester' })
  await loaded(container)
  await vi.waitFor(() => expect(container.querySelector('#endBrowser')).not.toBeDisabled())
  const end = screen.getByRole('button', { name: 'End tester' })
  await fireEvent.click(end)
  await vi.waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true))
  const del = calls.find((c) => c.method === 'DELETE')!
  expect(del.path).toBe('/admin/workspaces/k/session')
  expect(del.search).toBe('?session=tester')
})

test('End default ends the default with no session named', async () => {
  vi.stubGlobal('confirm', vi.fn(() => true))
  const { container, calls } = setup([DEFAULT, TESTER])
  await loaded(container)
  await fireEvent.click(screen.getByRole('button', { name: 'End default' }))
  await vi.waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true))
  expect(calls.find((c) => c.method === 'DELETE')!.search).toBe('')
})

test('End all lists each session, then ends them all', async () => {
  const { container, calls } = setup([DEFAULT, TESTER, ALEX])
  await loaded(container)
  await fireEvent.click(screen.getByRole('button', { name: 'End all' }))
  const sheet = document.querySelector('.modal') as HTMLElement
  expect(sheet).toHaveTextContent('End every session’s browser')
  expect(sheet).toHaveTextContent('default · firefox')
  expect(sheet).toHaveTextContent('tester · chrome')
  expect(sheet).toHaveTextContent('alex · idle, no browser')
  const confirm = within(sheet).getByRole('button', { name: 'End 2 browsers' })
  await fireEvent.click(confirm)
  await vi.waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === '/admin/workspaces/k/end-all')).toBe(true))
})

test('History filters by its own session control, all by default', async () => {
  const { container } = setup([DEFAULT, TESTER, ALEX], { tab: 'history', session: 'tester' })
  await loaded(container)
  const sites = () => [...container.querySelectorAll('#paneHistory section.section')]
  await vi.waitFor(() => expect(sites()).toHaveLength(4))
  const filter = screen.getByRole('combobox', { name: 'Session' }) as HTMLSelectElement
  // The chips do not drive it.
  expect(filter.value).toBe('')
  expect(within(filter).getByRole('option', { name: 'All sessions' })).toBeInTheDocument()
  expect(container.querySelector('#paneHistory')).not.toHaveTextContent('1 of 4')
  await fireEvent.change(filter, { target: { value: 'tester' } })
  expect(sites()).toHaveLength(1)
  expect(sites()[0]).toHaveTextContent('a.example.test')
  expect(container.querySelector('#paneHistory')).toHaveTextContent('1 of 4')
})

test('with only the default session History has no session filter', async () => {
  const { container } = setup([DEFAULT], { tab: 'history' })
  await vi.waitFor(() => expect(container.querySelectorAll('#paneHistory section.section')).toHaveLength(4))
  expect(screen.queryByRole('combobox', { name: 'Session' })).toBeNull()
})

test('Files groups downloads by session', async () => {
  const { container, calls } = setup([DEFAULT, TESTER], { tab: 'files' })
  await loaded(container)
  const groups = () => [...container.querySelectorAll('#downloads .group')]
  await vi.waitFor(() => expect(groups()).toHaveLength(2))
  expect(groups()[0]).toHaveTextContent('default')
  expect(groups()[0]).toHaveTextContent('d.pdf')
  expect(groups()[1]).toHaveTextContent('tester')
  expect(groups()[1]).toHaveTextContent('t.pdf')
  const sub = calls.find((c) => c.path === '/admin/workspaces/k/files/downloads')!
  expect(sub.search).toBe('?session=tester')
  expect(container.querySelector('#downloadsCount')).toHaveTextContent('2')
})

test('Files with only the default has no group labels', async () => {
  const { container } = setup([DEFAULT], { tab: 'files' })
  await vi.waitFor(() => expect(container.querySelector('#downloadsCount')).toHaveTextContent('1'))
  expect(container.querySelector('#downloads .group')).toBeNull()
})

const SITE = {
  key: 'k', saved_at: 1, uri: '',
  sites: [{ site: 't.example.test', uri: '', cookies: 1, storage: [] }],
  details: { 't.example.test': { site: 't.example.test', uri: '', cookies: [], storage: [], own_cookies: ['x'], kept_shared: [] } },
}
const sessionOf = (c: { search: string }) => new URLSearchParams(c.search).get('session')
const WORKSPACE_ONLY = ['/admin/workspaces/k/flows', '/admin/workspaces/k/history', '/admin/workspaces/k/config', '/admin/workspaces/k/files']

test('picking a chip changes the session tabs and not Files', async () => {
  history.replaceState(null, '', '/#/workspaces/k/console')
  const r = setup([DEFAULT, TESTER], { tab: 'console' })
  await loaded(r.container)
  await vi.waitFor(() => expect(r.calls.some((c) => c.path === '/admin/workspaces/k/console')).toBe(true))
  const mark = r.calls.length
  await fireEvent.click(screen.getByRole('radio', { name: /tester/ }))
  expect(location.hash).toBe('#/workspaces/k/console?session=tester')
  await r.rerender({ session: 'tester' })
  await vi.waitFor(() => expect(r.calls.slice(mark).some((c) => c.path === '/admin/workspaces/k/console')).toBe(true))
  const fresh = r.calls.slice(mark)
  for (const p of ['console', 'site-data', 'session']) {
    const c = fresh.find((x) => x.path === '/admin/workspaces/k/' + p)
    expect(c, p).toBeDefined()
    expect(sessionOf(c!), p).toBe('tester')
  }
  // Nothing the workspace owns follows the pick.
  for (const c of fresh.filter((x) => WORKSPACE_ONLY.includes(x.path))) expect(c.search).not.toContain('session')
  await vi.waitFor(() => expect(r.container.querySelector('#consoleTotal')).toHaveTextContent('7'))
  expect(r.container.querySelector('#sessionArea')).toHaveClass('sub')
  // A tab click keeps the pick in the address.
  await fireEvent.click(screen.getByRole('tab', { name: /Network/ }))
  expect(location.hash).toBe('#/workspaces/k/network?session=tester')
})

test('the Files tab ignores the pick: its own loads name no session', async () => {
  const { container, calls } = setup([DEFAULT, TESTER], { tab: 'files', session: 'tester' })
  await loaded(container)
  await vi.waitFor(() => expect(container.querySelector('#downloadsCount')).toHaveTextContent('2'))
  for (const p of ['files', 'flows', 'history']) for (const c of calls.filter((x) => x.path === '/admin/workspaces/k/' + p)) expect(c.search).not.toContain('session')
})

test('the network list and a request open under the picked session', async () => {
  stubEventSource()
  const detail = { ...req({ id: '1.1', status: 200 }), uri: 'workspace://network/1.1', request_headers: [], response_headers: [] }
  const body = { id: '1.1', uri: 'workspace://network/1.1', mime: 'text/plain', size: 2, binary: false, text: 'hi', truncated: false }
  const { container, calls } = setup([DEFAULT, TESTER], { tab: 'network', session: 'tester' }, {
    'GET /admin/workspaces/k/network': { body: networkData },
    'GET /admin/workspaces/k/network/1.1': { body: detail },
    'POST /admin/workspaces/k/network/1.1/body': { body },
  })
  await vi.waitFor(() => expect(calls.some((c) => c.path === '/admin/workspaces/k/network')).toBe(true))
  expect(sessionOf(calls.find((c) => c.path === '/admin/workspaces/k/network')!)).toBe('tester')
  expect(container.querySelector('#paneCapture')).not.toBeNull()
})

test('a request detail and its body are the picked session’s', async () => {
  const detail = { ...req({ id: '1.1', status: 200 }), uri: 'workspace://network/1.1', request_headers: [], response_headers: [] }
  const body = { id: '1.1', uri: 'workspace://network/1.1', mime: 'text/plain', size: 2, binary: false, text: 'hi', truncated: false }
  const { calls } = setup([DEFAULT, TESTER], { tab: 'network', session: 'tester', request: '1.1' }, {
    'GET /admin/workspaces/k/network/1.1': { body: detail },
    'POST /admin/workspaces/k/network/1.1/body': { body },
  })
  await fireEvent.click(await screen.findByRole('button', { name: 'Fetch body' }))
  await vi.waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
  const get = calls.find((c) => c.path === '/admin/workspaces/k/network/1.1')!
  const post = calls.find((c) => c.method === 'POST' && c.path.endsWith('/body'))!
  expect([sessionOf(get), sessionOf(post)]).toEqual(['tester', 'tester'])
})

test('the live view mints for the picked session', async () => {
  FakeSocket.all = []
  vi.stubGlobal('WebSocket', FakeSocket)
  const live = { ...TESTER_ROW, ran: { version: '4', node: 'n', live_view: true } }
  const { calls } = setup([DEFAULT, TESTER], { tab: 'live', session: 'tester', viewer: async () => ({ RFB: FakeRfb as never }) }, {
    'GET /admin/workspaces/k/session': { body: live },
    'GET /admin/workspaces/k/live': { body: { ...MINT } },
  })
  await vi.waitFor(() => expect(FakeSocket.all).toHaveLength(1))
  expect(sessionOf(calls.find((c) => c.path === '/admin/workspaces/k/live')!)).toBe('tester')
})

test('Site data forgets and clears in the picked session', async () => {
  const { container, calls } = setup([DEFAULT, TESTER], { tab: 'site-data', session: 'tester' }, {
    'GET /admin/workspaces/k/site-data': { body: SITE },
    'DELETE /admin/workspaces/k/site-data/t.example.test': { body: { forgotten: {} } },
    'DELETE /admin/workspaces/k/site-data': { body: { cleared: [] } },
  })
  await vi.waitFor(() => expect(container.querySelector('#paneSiteData section')).not.toBeNull())
  expect(sessionOf(calls.find((c) => c.method === 'GET' && c.path === '/admin/workspaces/k/site-data')!)).toBe('tester')
  const modal = () => document.querySelector('.modal') as HTMLElement
  await fireEvent.click(within(container.querySelector('#paneSiteData section') as HTMLElement).getByRole('button', { name: 'Forget' }))
  await fireEvent.click(within(modal()).getByRole('button', { name: 'Forget' }))
  await vi.waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.path.endsWith('/t.example.test'))).toBe(true))
  await fireEvent.click(within(container.querySelector('#paneSiteData .bar') as HTMLElement).getByRole('button', { name: 'Clear' }))
  await fireEvent.click(within(modal()).getByRole('button', { name: 'Clear' }))
  await vi.waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.path === '/admin/workspaces/k/site-data')).toBe(true))
  for (const c of calls.filter((x) => x.method === 'DELETE')) expect(sessionOf(c)).toBe('tester')
})

test('Keep on a sub-session download names that session', async () => {
  const { container, calls } = setup([DEFAULT, TESTER], { tab: 'files' }, {
    'POST /admin/workspaces/k/files/downloads/t.pdf/keep': { body: {} },
  })
  await vi.waitFor(() => expect(container.querySelector('#downloads [data-session=tester]')).toHaveTextContent('t.pdf'))
  await fireEvent.click(within(container.querySelector('#downloads [data-session=tester]') as HTMLElement).getByRole('button', { name: /Keep/ }))
  await vi.waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
  expect(sessionOf(calls.find((c) => c.method === 'POST')!)).toBe('tester')
})

test('each session’s downloads have their own Clear, live only, naming the session', async () => {
  const same = { 'GET /admin/workspaces/k/files/downloads': { body: { key: 'k', session: 'tester', browser: true, downloads: [file('report.pdf')] } } }
  const idle = { ...ALEX }
  const { container, calls } = setup([DEFAULT, TESTER, idle], { tab: 'files' }, {
    ...same,
    'GET /admin/workspaces/k/files': { body: { workspace: rowWith([DEFAULT, TESTER, idle]), downloads: [file('report.pdf')], screenshots: [], recordings: [], files: [], browser: true } },
    'DELETE /admin/workspaces/k/files/downloads': { status: 500, body: { error: 'grid said no' } },
  })
  await vi.waitFor(() => expect(container.querySelector('#downloads [data-session=tester]')).toHaveTextContent('report.pdf'))
  expect(container.querySelector('#clearDownloads')).toBeNull()
  expect(container.querySelector('#clearDownloads-default')).not.toBeDisabled()
  expect(container.querySelector('#clearDownloads-tester')).not.toBeDisabled()
  expect(container.querySelector('#clearDownloads-alex')).toBeDisabled()
  const errors: string[] = []
  const warn = vi.spyOn(console, 'error').mockImplementation((m) => { errors.push(String(m)) })
  await fireEvent.click(container.querySelector('#clearDownloads-tester')!)
  const sheet = document.querySelector('.modal') as HTMLElement
  expect(sheet).toHaveTextContent('Clear tester downloads')
  expect(sheet).toHaveTextContent('the tester session’s browser')
  expect(sheet.querySelectorAll('li')).toHaveLength(1)
  await fireEvent.click(within(sheet).getByRole('button', { name: /Clear 1 file/ }))
  await vi.waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true))
  // One DELETE, for that session alone; the default's same-named file is untouched.
  expect(calls.filter((c) => c.method === 'DELETE').map((c) => c.path + c.search)).toEqual(['/admin/workspaces/k/files/downloads?session=tester'])
  expect(errors.filter((e) => e.includes('each_key_duplicate'))).toEqual([])
  warn.mockRestore()
})

test('a failing clear leaves the other sessions alone and says so once', async () => {
  const { container, calls } = setup([DEFAULT, TESTER], { tab: 'files' }, {
    'DELETE /admin/workspaces/k/files/downloads': { status: 500, body: { error: 'grid said no' } },
  })
  await vi.waitFor(() => expect(container.querySelector('#downloads [data-session=tester]')).toHaveTextContent('t.pdf'))
  await fireEvent.click(container.querySelector('#clearDownloads-default')!)
  await fireEvent.click(within(document.querySelector('.modal') as HTMLElement).getByRole('button', { name: /Clear 1 file/ }))
  await vi.waitFor(() => expect(alert).toHaveBeenCalledTimes(1))
  expect(alert).toHaveBeenCalledWith('grid said no')
  expect(calls.filter((c) => c.method === 'DELETE')).toHaveLength(1)
})

test('the Session tab shows the picked session’s last page, a link only for http(s)', async () => {
  const dflt = rowWith([DEFAULT, TESTER])
  const { container } = setup([DEFAULT, TESTER], { tab: 'session' }, {
    'GET /admin/workspaces/k/files': { body: { workspace: { ...dflt, url: 'https://default.example.test/a' }, downloads: [], screenshots: [], recordings: [], files: [], browser: true } },
  })
  await vi.waitFor(() => expect(container.querySelector('#workspaceCard')).toHaveTextContent('https://default.example.test/a'))
  expect(within(container.querySelector('#workspaceCard') as HTMLElement).getByRole('link', { name: 'https://default.example.test/a' })).toHaveAttribute('rel', 'noopener noreferrer')
  const r = setup([DEFAULT, TESTER], { tab: 'session', session: 'tester' }, {
    'GET /admin/workspaces/k/session': { body: { ...TESTER_ROW, url: 'javascript:x' } },
  })
  await vi.waitFor(() => expect(r.container.querySelector('#workspaceCard')).toHaveTextContent('javascript:x'))
  expect(within(r.container.querySelector('#workspaceCard') as HTMLElement).queryByRole('link')).toBeNull()
})
