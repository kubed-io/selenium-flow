import { fireEvent, render, screen, within } from '@testing-library/svelte'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { fakeFetch } from '../test/helpers'
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

test('picking a chip changes the session tabs and not Files', async () => {
  const { container, calls } = setup([DEFAULT, TESTER], { tab: 'console', session: 'tester' })
  await loaded(container)
  await vi.waitFor(() => expect(calls.some((c) => c.path === '/admin/workspaces/k/console')).toBe(true))
  // Console, Site data and the Session row follow the pick.
  expect(calls.find((c) => c.path === '/admin/workspaces/k/console')!.search).toContain('session=tester')
  expect(calls.find((c) => c.path === '/admin/workspaces/k/site-data')!.search).toContain('session=tester')
  expect(calls.find((c) => c.path === '/admin/workspaces/k/session')!.search).toBe('?session=tester')
  // The count is the picked session's.
  expect(container.querySelector('#consoleTotal')).toHaveTextContent('7')
  // Files, Flows and History never do.
  for (const p of ['files', 'flows', 'history']) {
    for (const c of calls.filter((x) => x.path === '/admin/workspaces/k/' + p)) expect(c.search).not.toContain('session')
  }
  // A tab click keeps the pick in the address.
  await fireEvent.click(screen.getByRole('tab', { name: /Network/ }))
  expect(location.hash).toBe('#/workspaces/k/network?session=tester')
  // The picked session marks the session area, never a line of text.
  expect(container.querySelector('#sessionArea')).toHaveClass('sub')
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

test('Live and Site data calls name the picked session', async () => {
  const { container, calls } = setup([DEFAULT, TESTER], { tab: 'site-data', session: 'tester' }, {
    'DELETE /admin/workspaces/k/site-data': { body: { success: true } },
  })
  await loaded(container)
  await vi.waitFor(() => expect(calls.some((c) => c.path === '/admin/workspaces/k/site-data')).toBe(true))
  expect(calls.find((c) => c.path === '/admin/workspaces/k/site-data')!.search).toContain('session=tester')
})
