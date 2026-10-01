import { fireEvent, render, screen, within } from '@testing-library/svelte'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { deferred, fakeFetch } from '../test/helpers'
import { createApi } from './api'
import { resetFolds } from './folds.svelte'
import { Live } from './live.svelte'
import SessionDetail from './SessionDetail.svelte'

const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })
beforeEach(() => { history.replaceState(null, '', '/#/sessions/k/history') })
afterEach(() => resetFolds())

const now = Date.now() / 1000
const HISTORY = {
  key: 'k',
  sites: [
    { site: 'the-internet.herokuapp.com', url: 'https://the-internet.herokuapp.com/secure', at: now - 60,
      saved: { cookies: 2, local: 2, session: 0 },
      secrets: [{ name: 'the-internet', description: 'Public demo login', keys: ['username', 'password'] }] },
    { site: 'grafana.example.com', url: 'https://grafana.example.com/d/selenium-grid', at: now - 3600,
      saved: { cookies: 5, local: 12, session: 0 },
      secrets: [{ name: 'grafana', description: 'LDAP sign-in for Grafana', keys: ['username', 'password'] }] },
    { site: 'example.com', url: 'https://example.com/', at: now - 3 * 3600, saved: null, secrets: [] },
    { site: 'admin.example.com', url: 'https://admin.example.com/flow/', at: now - 5 * 3600, saved: null,
      secrets: [{ name: 'selenium-admin', description: 'Admin token for this server', keys: ['token'] }] },
  ],
}
const counted = (origin: string, local: number) => ({ origin, local_storage: local, session_storage: 0 })
const empty = (site: string) => ({ site, uri: '', cookies: [], storage: [], own_cookies: [], kept_shared: [] })
const SITE_DATA = {
  key: 'k', saved_at: now - 120, uri: '',
  sites: [
    { site: 'the-internet.herokuapp.com', uri: '', cookies: 2, storage: [counted('https://the-internet.herokuapp.com', 2)] },
    { site: 'grafana.example.com', uri: '', cookies: 5, storage: [counted('https://grafana.example.com', 12)] },
  ],
  details: { 'the-internet.herokuapp.com': empty('the-internet.herokuapp.com'), 'grafana.example.com': empty('grafana.example.com') },
}
const row = { key: 'k', name: 'mine', live: true, attached: true, session_id: 'b1', files_rev: 1, flows_rev: 1, files_count: 0, history_count: 4, history_rev: 'h1' }

function setup(routes = {}) {
  const net = fakeFetch({
    'GET /admin/sessions/k/files': { body: { session: row, downloads: [], screenshots: [], files: [], browser: true } },
    'GET /admin/sessions/k/flows': { body: { enabled: true, flows: [], rev: 1, session: 'k' } },
    'GET /admin/sessions/k/site-data': { body: SITE_DATA },
    'GET /admin/sessions/k/history': { body: HISTORY },
    ...routes,
  })
  const live = new Live(api, '')
  const r = render(SessionDetail, { key: 'k', tab: 'history', flow: undefined, api, live, root: '' })
  return { ...r, ...net, live }
}
const visits = (c: HTMLElement) => [...c.querySelectorAll('#paneHistory section.section')] as HTMLElement[]
const loaded = (c: HTMLElement, n = 4) => vi.waitFor(() => expect(visits(c)).toHaveLength(n))
const modal = () => document.querySelector('.modal') as HTMLElement

test('History is the fourth subtab and counts the hosts listed', async () => {
  const { container } = setup()
  const tabs = [...container.querySelectorAll('#sessionTabs button')].map((b) => b.firstChild!.textContent!.trim())
  expect(tabs).toEqual(['Files', 'Flows', 'Site data', 'History'])
  expect(screen.getByRole('tab', { name: /History/ })).toHaveAttribute('aria-selected', 'true')
  await loaded(container)
  expect(container.querySelector('#historyTotal')).toHaveTextContent('4')
})

test('the current site is on top: its URL, when, and what is saved there', async () => {
  const { container } = setup()
  await loaded(container)
  const [a, b, c] = visits(container)
  expect(a.querySelector('.title')).toHaveTextContent('https://the-internet.herokuapp.com/secure')
  expect(a.querySelector('.head')).toHaveTextContent('1m ago · 1 secret')
  expect(a.querySelector('.head .pill')).toHaveTextContent('2 cookies · 2 local')
  expect(b.querySelector('.head')).toHaveTextContent('1h ago · 1 secret')
  expect(b.querySelector('.head .pill')).toHaveTextContent('5 cookies · 12 local')
  expect(c.querySelector('.title')).toHaveTextContent('https://example.com/')
  expect(c.querySelector('.head')).not.toHaveTextContent('secret')
  expect(c.querySelector('.head .pill')).toBeNull()
})

test('rows fold like Site data: the top one opens to its secrets, the rest stay shut', async () => {
  const { container } = setup()
  await loaded(container)
  const [a, b, c] = visits(container)
  expect(a.querySelector('.title')).toHaveAttribute('aria-expanded', 'true')
  const line = a.querySelector('.line.secret')!
  expect(line).toHaveTextContent('🔑 the-internet')
  expect(line).toHaveTextContent('Public demo login')
  expect([...line.querySelectorAll('.pill')].map((p) => p.textContent)).toEqual(['username', 'password'])
  expect(b.querySelector('.title')).toHaveAttribute('aria-expanded', 'false')
  expect(b.querySelector('.line.secret')).toBeNull()
  await fireEvent.click(b.querySelector('.title')!)
  await vi.waitFor(() => expect(b.querySelector('.line.secret')).toHaveTextContent('🔑 grafana'))
  // Nothing to open: no caret button at all.
  expect(c.querySelector('[aria-expanded]')).toBeNull()
  expect(c.querySelector('.line.secret')).toBeNull()
})

test('the saved pill opens that host in Site data, unfolded', async () => {
  const { container, rerender } = setup()
  await loaded(container)
  await fireEvent.click(visits(container)[1].querySelector('.head .pill')!)
  expect(location.hash).toBe('#/sessions/k/site-data/grafana.example.com')
  await rerender({ tab: 'site-data', site: 'grafana.example.com' })
  const grafana = () => [...container.querySelectorAll('#paneSiteData section.section')]
    .find((s) => s.querySelector('.title')!.textContent!.includes('grafana')) as HTMLElement
  await vi.waitFor(() => expect(grafana().querySelector('.title')).toHaveAttribute('aria-expanded', 'true'))
})

test('a second link to the same host opens it again, once the reader has shut it', async () => {
  const { container, rerender } = setup()
  await loaded(container)
  const grafana = () => [...container.querySelectorAll('#paneSiteData section.section')]
    .find((s) => s.querySelector('.title')!.textContent!.includes('grafana')) as HTMLElement
  const title = () => grafana().querySelector('.title')!
  await rerender({ tab: 'site-data', site: 'grafana.example.com' })
  await vi.waitFor(() => expect(title()).toHaveAttribute('aria-expanded', 'true'))
  await fireEvent.click(title())
  await vi.waitFor(() => expect(title()).toHaveAttribute('aria-expanded', 'false'))
  await rerender({ tab: 'history', site: undefined })
  await rerender({ tab: 'site-data', site: 'grafana.example.com' })
  await vi.waitFor(() => expect(title()).toHaveAttribute('aria-expanded', 'true'))
})

test('Clear is offered only while there is more than the current site', async () => {
  const { container, unmount } = setup()
  await loaded(container)
  expect(container.querySelector('#clearHistory')).not.toBeNull()
  unmount()
  const again = setup({ 'GET /admin/sessions/k/history': { body: { key: 'k', sites: HISTORY.sites.slice(0, 1) } } })
  await loaded(again.container, 1)
  expect(again.container.querySelector('#clearHistory')).toBeNull()
})

test('Clear confirms what goes and what stays, then DELETEs the history and reloads', async () => {
  const { container, calls } = setup({ 'DELETE /admin/sessions/k/history': { body: { cleared: [] } } })
  await loaded(container)
  await fireEvent.click(container.querySelector('#clearHistory')!)
  expect(modal().querySelector('.head')).toHaveTextContent('Clear history')
  expect(modal()).toHaveTextContent('History only: site data and the browser are untouched.')
  expect([...modal().querySelectorAll('.pair')].map((p) => p.textContent)).toEqual([
    'goesgrafana.example.com · example.com · admin.example.com',
    'staysthe-internet.herokuapp.com, the current site',
  ])
  const gets = () => calls.filter((c) => c.method === 'GET' && c.path.endsWith('/history')).length
  const before = gets()
  await fireEvent.click(within(modal()).getByRole('button', { name: 'Clear' }))
  await vi.waitFor(() => expect(calls.filter((c) => c.method === 'DELETE').map((c) => c.path)).toEqual(['/admin/sessions/k/history']))
  await vi.waitFor(() => expect(gets()).toBe(before + 1))
})

test('a pushed history_rev change reloads; the same rev does not', async () => {
  const { container, calls, live } = setup()
  await loaded(container)
  const gets = () => calls.filter((c) => c.method === 'GET' && c.path.endsWith('/history')).length
  expect(gets()).toBe(1)
  live.data = { sessions: [{ ...row, history_rev: 'h1' }] }
  await new Promise((r) => setTimeout(r, 10))
  expect(gets()).toBe(1)
  live.data = { sessions: [{ ...row, history_rev: 'h2' }] }
  await vi.waitFor(() => expect(gets()).toBe(2))
})

test('Loading… first, an error in the pane, and nowhere yet when empty', async () => {
  const d = deferred<{ status: number; body: unknown }>()
  const { container, unmount } = setup({ 'GET /admin/sessions/k/history': () => d.promise })
  expect(within(container.querySelector('#paneHistory') as HTMLElement).getByText('Loading…')).toBeInTheDocument()
  d.resolve({ status: 500, body: { error: 'nope' } })
  await vi.waitFor(() => expect(screen.getByText('nope')).toHaveClass('error'))
  unmount()
  const none = setup({ 'GET /admin/sessions/k/history': { body: { key: 'k', sites: [] } } })
  await vi.waitFor(() => expect(none.container.querySelector('#paneHistory')).toHaveTextContent('Nowhere yet.'))
})
