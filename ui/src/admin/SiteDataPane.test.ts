import { fireEvent, render, screen, within } from '@testing-library/svelte'
import { afterEach, expect, test, vi } from 'vitest'
import { deferred, fakeFetch } from '../test/helpers'
import { createApi } from './api'
import { resetFolds } from './folds.svelte'
import { Live } from './live.svelte'
import WorkspaceDetail from './WorkspaceDetail.svelte'

const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })
afterEach(() => resetFolds())

const cookie = (name: string, value: string, extra = {}) =>
  ({ name, value, domain: 'the-internet.herokuapp.com', path: '/', expiry: null, http_only: false, secure: false, same_site: null, shared: false, ...extra })
const ORIGIN = 'https://the-internet.herokuapp.com'
const GRAFANA = 'https://grafana.example.com'
const detail = {
  site: 'the-internet.herokuapp.com', uri: 'workspace://site-data/the-internet.herokuapp.com',
  cookies: [
    cookie('rack.session', '•••', { http_only: true, secure: true }),
    cookie('optimizelyEndUserId', 'oeu1', { domain: '.herokuapp.com', expiry: 1790800000, shared: true }),
  ],
  storage: [{ origin: ORIGIN, local_storage: { theme: 'dark', 'tour-seen': 'true' } as Record<string, string>, session_storage: {} as Record<string, string> }],
  own_cookies: ['rack.session'], kept_shared: [{ name: 'optimizelyEndUserId', domain: '.herokuapp.com', path: '/' }],
}
const counted = (origin: string, local: number, session = 0) => ({ origin, local_storage: local, session_storage: session })
const SITES = {
  key: 'k', saved_at: Date.now() / 1000 - 120, uri: 'workspace://site-data',
  sites: [
    { site: 'the-internet.herokuapp.com', uri: '', cookies: 2, storage: [counted(ORIGIN, 2)] },
    { site: 'grafana.example.com', uri: '', cookies: 5, storage: [counted(GRAFANA, 12)] },
    { site: 'keycloak.example.com', uri: '', cookies: 3, storage: [] },
  ],
  details: {
    'the-internet.herokuapp.com': detail,
    'grafana.example.com': { ...detail, site: 'grafana.example.com', cookies: [], storage: [{ origin: GRAFANA, local_storage: {}, session_storage: {} }], own_cookies: [], kept_shared: [] },
    'keycloak.example.com': { site: 'keycloak.example.com', uri: '', cookies: [cookie('KC', 'k', { domain: 'keycloak.example.com' })], storage: [], own_cookies: ['KC'], kept_shared: [] },
  },
}
const row = { key: 'k', name: 'mine', live: true, attached: true, session_id: 'b1', files_rev: 1, flows_rev: 1, files_count: 0, site_data_count: 3, site_data_rev: 'r1' }
const base = {
  'GET /admin/workspaces/k/files': { body: { workspace: row, downloads: [], screenshots: [], files: [], browser: true } },
  'GET /admin/workspaces/k/flows': { body: { enabled: true, flows: [], rev: 1, workspace: 'k' } },
  'GET /admin/workspaces/k/history': { body: { key: 'k', sites: [] } },
}

function setup(routes = {}) {
  const net = fakeFetch({ ...base, 'GET /admin/workspaces/k/site-data': { body: SITES }, ...routes })
  const live = new Live(api, '')
  const r = render(WorkspaceDetail, { key: 'k', tab: 'site-data', flow: undefined, api, live, root: '' })
  return { ...r, ...net, live }
}
const sections = (c: HTMLElement) => [...c.querySelectorAll('#paneSiteData section.section')] as HTMLElement[]
const loaded = (c: HTMLElement, n = 3) => vi.waitFor(() => expect(sections(c)).toHaveLength(n))
const modal = () => document.querySelector('.modal') as HTMLElement
const pairs = () => [...modal().querySelectorAll('.pair')].map((p) => p.textContent)

test('the subtab counts the hosts, and rows come in order with their counts', async () => {
  const { container } = setup()
  expect(screen.getByRole('tab', { name: /Site data/ })).toHaveAttribute('aria-selected', 'true')
  await loaded(container)
  expect(container.querySelector('#siteDataTotal')).toHaveTextContent('3')
  const [a, b, c] = sections(container)
  expect(a.querySelector('.title')).toHaveTextContent(ORIGIN)
  expect(a.querySelector('.head')).toHaveTextContent('2 cookies · 2 local · 0 session')
  expect(b.querySelector('.head')).toHaveTextContent('5 cookies · 12 local · 0 session')
  expect(c.querySelector('.title')).toHaveTextContent('keycloak.example.com')
  expect(c.querySelector('.head')).toHaveTextContent('3 cookies')
  expect(c.querySelector('.head')).not.toHaveTextContent('local')
  expect(a.querySelector('.head .pill')).toBeNull()
})

test('one saved pill and Clear sit above the rows', async () => {
  const { container } = setup()
  await loaded(container)
  const bar = container.querySelector('#paneSiteData .bar') as HTMLElement
  expect(bar.querySelector('.pill')).toHaveTextContent('saved 2m ago')
  expect(within(bar).getByRole('button', { name: 'Clear' })).toHaveClass('danger')
})

test('the first row is open, the others shut', async () => {
  const { container } = setup()
  await loaded(container)
  const [a, b, c] = sections(container)
  expect(a.querySelector('.title')).toHaveAttribute('aria-expanded', 'true')
  expect(b.querySelector('.title')).toHaveAttribute('aria-expanded', 'false')
  expect(c.querySelector('.title')).toHaveAttribute('aria-expanded', 'false')
})

test('expanded: cookies with dots for httpOnly, flags, expiry; storage; none', async () => {
  const { container } = setup()
  await loaded(container)
  const a = within(sections(container)[0])
  const rack = a.getByText('rack.session').closest('.line') as HTMLElement
  expect(rack).toHaveTextContent('•••')
  expect(rack).toHaveTextContent('the-internet.herokuapp.com · session')
  expect([...rack.querySelectorAll('.pill')].map((p) => p.textContent)).toEqual(['httpOnly', 'secure'])
  const opt = a.getByText('optimizelyEndUserId').closest('.line') as HTMLElement
  expect(opt).toHaveTextContent(new Date(1790800000 * 1000).toLocaleDateString())
  expect(opt.querySelector('.pill.shared')).toHaveTextContent('shared')
  expect(a.getByText('theme').closest('.line')).toHaveTextContent('dark')
  // One line each, the whole value on hover: a long cookie wrapped to seven.
  const value = opt.querySelector('.value') as HTMLElement
  expect(value).toHaveClass('clip')
  expect(value).toHaveAttribute('title', value.textContent!)
  expect(a.getByText('Session storage').nextElementSibling!).toHaveTextContent('none')
})

test('the shared pill follows the payload: a site\'s own dotted cookie has none', async () => {
  const own = cookie('own', 'o', { domain: '.the-internet.herokuapp.com', shared: false })
  const d = { ...detail, cookies: [own, ...detail.cookies.slice(1)] }
  const { container } = setup({ 'GET /admin/workspaces/k/site-data': { body: { ...SITES, details: { ...SITES.details, [detail.site]: d } } } })
  await loaded(container)
  const a = within(sections(container)[0])
  expect(a.getByText('own').closest('.line')!.querySelector('.pill.shared')).toBeNull()
  expect(a.getByText('optimizelyEndUserId').closest('.line')!.querySelector('.pill.shared')).not.toBeNull()
})

test('every row has Forget, and the tab shows no secrets and no "nothing saved"', async () => {
  const { container } = setup()
  await loaded(container)
  for (const s of sections(container)) expect(within(s).getByRole('button', { name: 'Forget' })).toBeInTheDocument()
  await fireEvent.click(sections(container)[2].querySelector('.title')!)
  const pane = container.querySelector('#paneSiteData') as HTMLElement
  expect(pane.querySelector('.line.secret')).toBeNull()
  expect(pane).not.toHaveTextContent('nothing saved')
  expect(pane).not.toHaveTextContent('It stays listed because a secret is allowed here.')
})

test('nothing saved: the drawn line, no pill, no Clear, a count of 0', async () => {
  const none = { key: 'k', saved_at: null, uri: 'workspace://site-data', sites: [], details: {} }
  const { container } = setup({ 'GET /admin/workspaces/k/site-data': { body: none } })
  await vi.waitFor(() => expect(container.querySelector('#paneSiteData > p')).not.toBeNull())
  const line = container.querySelector('#paneSiteData > p')!
  expect(line).toHaveTextContent('Nothing saved — an agent calls save_site_data after signing in.')
  expect(line.querySelector('code')).toHaveTextContent('save_site_data')
  expect(container.querySelector('#paneSiteData .bar')).toBeNull()
  expect(container.querySelector('#siteDataTotal')).toHaveTextContent('0')
})

test('Forget confirms with goes and stays, then DELETEs the site and reloads', async () => {
  const { container, calls } = setup({ 'DELETE /admin/workspaces/k/site-data/the-internet.herokuapp.com': { body: { forgotten: {} } } })
  await loaded(container)
  await fireEvent.click(within(sections(container)[0]).getByRole('button', { name: 'Forget' }))
  expect(modal().querySelector('.head')).toHaveTextContent('Forget site data')
  expect(modal()).toHaveTextContent(ORIGIN + ' — a reopened browser comes back signed out here.')
  expect(pairs()).toEqual([
    'goesrack.session · theme · tour-seen',
    'staysoptimizelyEndUserId, shared with .herokuapp.com',
  ])
  expect(calls.filter((c) => c.method === 'DELETE')).toHaveLength(0)
  const gets = () => calls.filter((c) => c.method === 'GET' && c.path.endsWith('/site-data')).length
  const before = gets()
  await fireEvent.click(within(modal()).getByRole('button', { name: 'Forget' }))
  await vi.waitFor(() => expect(calls.filter((c) => c.method === 'DELETE').map((c) => c.path)).toEqual(['/admin/workspaces/k/site-data/the-internet.herokuapp.com']))
  await vi.waitFor(() => expect(gets()).toBe(before + 1))
})

test('Clear confirms with the hosts, then DELETEs the snapshot and reloads', async () => {
  const { container, calls } = setup({ 'DELETE /admin/workspaces/k/site-data': { body: { cleared: [] } } })
  await loaded(container)
  await fireEvent.click(within(container.querySelector('#paneSiteData .bar') as HTMLElement).getByRole('button', { name: 'Clear' }))
  expect(modal().querySelector('.head')).toHaveTextContent('Clear site data')
  expect(modal()).toHaveTextContent('3 sites — a reopened browser comes back signed out.')
  expect(pairs()).toEqual(['goesthe-internet.herokuapp.com · grafana.example.com · keycloak.example.com'])
  const gets = () => calls.filter((c) => c.method === 'GET' && c.path.endsWith('/site-data')).length
  const before = gets()
  await fireEvent.click(within(modal()).getByRole('button', { name: 'Clear' }))
  await vi.waitFor(() => expect(calls.filter((c) => c.method === 'DELETE').map((c) => c.path)).toEqual(['/admin/workspaces/k/site-data']))
  await vi.waitFor(() => expect(gets()).toBe(before + 1))
})

test('Forget and Clear reload History too: its saved pills would point at a gone row', async () => {
  const { container, calls } = setup({
    'DELETE /admin/workspaces/k/site-data/the-internet.herokuapp.com': { body: { forgotten: {} } },
    'DELETE /admin/workspaces/k/site-data': { body: { cleared: [] } },
  })
  await loaded(container)
  const history = () => calls.filter((c) => c.method === 'GET' && c.path.endsWith('/history')).length
  await vi.waitFor(() => expect(history()).toBe(1)) // the first load
  await fireEvent.click(within(sections(container)[0]).getByRole('button', { name: 'Forget' }))
  await fireEvent.click(within(modal()).getByRole('button', { name: 'Forget' }))
  await vi.waitFor(() => expect(history()).toBe(2))
  await fireEvent.click(within(container.querySelector('#paneSiteData .bar') as HTMLElement).getByRole('button', { name: 'Clear' }))
  await fireEvent.click(within(modal()).getByRole('button', { name: 'Clear' }))
  await vi.waitFor(() => expect(history()).toBe(3))
})

test('Cancel forgets and clears nothing', async () => {
  const { container, calls } = setup()
  await loaded(container)
  await fireEvent.click(within(sections(container)[1]).getByRole('button', { name: 'Forget' }))
  await fireEvent.click(within(modal()).getByRole('button', { name: 'Cancel' }))
  await fireEvent.click(within(container.querySelector('#paneSiteData .bar') as HTMLElement).getByRole('button', { name: 'Clear' }))
  await fireEvent.click(within(modal()).getByRole('button', { name: 'Cancel' }))
  expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
})

test('a pushed site_data_rev change reloads; the same rev does not', async () => {
  const { container, calls, live } = setup()
  await loaded(container)
  const gets = () => calls.filter((c) => c.method === 'GET' && c.path.endsWith('/site-data')).length
  expect(gets()).toBe(1)
  const push = (rev: string) => { live.data = { workspaces: [{ ...row, site_data_rev: rev }] } }
  push('r1')
  await new Promise((r) => setTimeout(r, 10))
  expect(gets()).toBe(1)
  push('r2')
  await vi.waitFor(() => expect(gets()).toBe(2))
})

test('a failed load is retried by the next push at the same rev', async () => {
  let n = 0
  const { container, live } = setup({
    'GET /admin/workspaces/k/site-data': () => (++n <= 2 ? { status: 500, body: { error: 'flaky' } } : { body: SITES }),
  })
  await vi.waitFor(() => expect(screen.getByText('flaky')).toBeInTheDocument())
  live.data = { workspaces: [{ ...row, site_data_rev: 'r1' }] }
  await vi.waitFor(() => expect(n).toBe(2))
  await new Promise((r) => setTimeout(r, 10))
  live.data = { workspaces: [{ ...row, site_data_rev: 'r1', files_count: 1 }] }
  await loaded(container)
  expect(n).toBe(3)
})

test('an error shows in the pane, Loading… before', async () => {
  const d = deferred<{ status: number; body: unknown }>()
  const { container } = setup({ 'GET /admin/workspaces/k/site-data': () => d.promise })
  expect(within(container.querySelector('#paneSiteData') as HTMLElement).getByText('Loading…')).toBeInTheDocument()
  d.resolve({ status: 500, body: { error: 'nope' } })
  await vi.waitFor(() => expect(screen.getByText('nope')).toHaveClass('error'))
})

test('a parent-only row: its dotted cookie goes', async () => {
  const PARENT = cookie('shared', 's', { domain: '.example.com', shared: false })
  const data = {
    key: 'k', saved_at: Date.now() / 1000 - 60, uri: '',
    sites: [{ site: 'example.com', uri: '', cookies: 1, storage: [] }],
    details: { 'example.com': { site: 'example.com', uri: '', cookies: [PARENT], storage: [], own_cookies: ['shared'], kept_shared: [] } },
  }
  const { container } = setup({ 'GET /admin/workspaces/k/site-data': { body: data } })
  await loaded(container, 1)
  await fireEvent.click(within(sections(container)[0]).getByRole('button', { name: 'Forget' }))
  expect(pairs()).toEqual(['goesshared'])
})

test('after Forget the tab counts the reloaded payload, before any push', async () => {
  let n = 0
  const after = { ...SITES, sites: SITES.sites.slice(1) }
  const { container } = setup({
    'GET /admin/workspaces/k/site-data': () => ({ body: ++n === 1 ? SITES : after }),
    'DELETE /admin/workspaces/k/site-data/the-internet.herokuapp.com': { body: { forgotten: {} } },
  })
  await loaded(container)
  expect(container.querySelector('#siteDataTotal')).toHaveTextContent('3')
  await fireEvent.click(within(sections(container)[0]).getByRole('button', { name: 'Forget' }))
  await fireEvent.click(within(modal()).getByRole('button', { name: 'Forget' }))
  await loaded(container, 2)
  expect(container.querySelector('#siteDataTotal')).toHaveTextContent('2')
})

test('forgetting the last host leaves the empty line, not a pill: saved_at stays set', async () => {
  let n = 0
  const one = { ...SITES, sites: SITES.sites.slice(0, 1) }
  const emptied = { ...SITES, sites: [], details: {} }
  const { container } = setup({
    'GET /admin/workspaces/k/site-data': () => ({ body: ++n === 1 ? one : emptied }),
    'DELETE /admin/workspaces/k/site-data/the-internet.herokuapp.com': { body: { forgotten: {} } },
  })
  await loaded(container, 1)
  expect(container.querySelector('#paneSiteData .bar .pill')).toHaveTextContent('saved 2m ago')
  await fireEvent.click(within(sections(container)[0]).getByRole('button', { name: 'Forget' }))
  await fireEvent.click(within(modal()).getByRole('button', { name: 'Forget' }))
  await vi.waitFor(() => expect(container.querySelector('#paneSiteData > p')).not.toBeNull())
  expect(emptied.saved_at).toBeGreaterThan(0)
  expect(container.querySelector('#paneSiteData .bar')).toBeNull()
  expect(container.querySelector('#paneSiteData .pill')).toBeNull()
  expect(within(container.querySelector('#paneSiteData') as HTMLElement).queryByRole('button', { name: 'Clear' })).toBeNull()
  expect(container.querySelector('#siteDataTotal')).toHaveTextContent('0')
})

test('every id in the pane is unique, and each fold controls its own body', async () => {
  const { container } = setup()
  await loaded(container)
  const ids = [...container.querySelectorAll('#paneSiteData [id]')].map((e) => e.id)
  expect(new Set(ids).size).toBe(ids.length)
  for (const s of sections(container)) {
    const body = s.querySelector('.title')!.getAttribute('aria-controls')!
    expect(body).not.toBe(s.id)
    expect(s.querySelector('.body')!.id).toBe(body)
  }
})

test('two shared cookies of one name both stay, each under its own domain', async () => {
  const sid = (domain: string, path = '/') => ({ name: 'sid', domain, path })
  const data = {
    key: 'k', saved_at: Date.now() / 1000, uri: '',
    sites: [{ site: 'app.example.com', uri: '', cookies: 2, storage: [counted('https://app.example.com', 1)] }],
    details: {
      'app.example.com': {
        site: 'app.example.com', uri: '', cookies: [],
        storage: [{ origin: 'https://app.example.com', local_storage: { a: '1' }, session_storage: {} }],
        own_cookies: [], kept_shared: [sid('.example.com'), sid('.example.org', '/app')],
      },
    },
  }
  const { container } = setup({ 'GET /admin/workspaces/k/site-data': { body: data } })
  await loaded(container, 1)
  await fireEvent.click(within(sections(container)[0]).getByRole('button', { name: 'Forget' }))
  expect(pairs()).toEqual([
    'goesa',
    'stayssid, shared with .example.com',
    'stayssid, shared with .example.org /app',
  ])
})

test('one host on two ports: a storage group per origin, each labelled, the host as the title', async () => {
  const dev = (port: number, local: Record<string, string>) => ({ origin: 'http://localhost:' + port, local_storage: local, session_storage: {} })
  const data = {
    key: 'k', saved_at: Date.now() / 1000, uri: '',
    sites: [{ site: 'localhost', uri: '', cookies: 0, storage: [counted('http://localhost:3000', 1), counted('http://localhost:8080', 1)] }],
    details: { localhost: { site: 'localhost', uri: '', cookies: [], own_cookies: [], kept_shared: [], storage: [dev(3000, { k: 'a' }), dev(8080, { k: 'b' })] } },
  }
  const { container } = setup({ 'GET /admin/workspaces/k/site-data': { body: data } })
  await loaded(container, 1)
  const [s] = sections(container)
  expect(s.querySelector('.title')).toHaveTextContent('localhost')
  expect(s.querySelector('.head')).toHaveTextContent('0 cookies · 2 local · 0 session')
  expect([...s.querySelectorAll('h3')].map((h) => h.textContent)).toEqual([
    'Cookies',
    'Local storage · http://localhost:3000', 'Session storage · http://localhost:3000',
    'Local storage · http://localhost:8080', 'Session storage · http://localhost:8080',
  ])
  const values = [...s.querySelectorAll('.line')].filter((l) => l.querySelector('code.key')?.textContent === 'k').map((l) => l.querySelector('.value')!.textContent)
  expect(values).toEqual(['a', 'b'])
})
