import { fireEvent, render, screen, within } from '@testing-library/svelte'
import { afterEach, expect, test, vi } from 'vitest'
import { deferred, fakeFetch } from '../test/helpers'
import { createApi } from './api'
import { resetFolds } from './folds.svelte'
import { Live } from './live.svelte'
import SessionDetail from './SessionDetail.svelte'

const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })
afterEach(() => resetFolds())

const THE_INTERNET = { name: 'the-internet', description: 'Public demo login', keys: ['username', 'password'] }
const cookie = (name: string, value: string, extra = {}) =>
  ({ name, value, domain: 'the-internet.herokuapp.com', path: '/', expiry: null, http_only: false, secure: false, same_site: null, shared: false, ...extra })
const detail = {
  site: 'the-internet.herokuapp.com', origin: 'https://the-internet.herokuapp.com', saved: true, saved_at: Date.now() / 1000 - 120,
  cookies: [
    cookie('rack.session', '•••', { http_only: true, secure: true }),
    cookie('optimizelyEndUserId', 'oeu1', { domain: '.herokuapp.com', expiry: 1790800000, shared: true }),
  ],
  local_storage: { theme: 'dark', 'tour-seen': 'true' }, session_storage: {}, secrets: [THE_INTERNET],
  own_cookies: ['rack.session'], kept_shared: ['optimizelyEndUserId'],
}
const summary = (d: typeof detail) => ({ ...d, cookies: d.cookies.length, local_storage: 2, session_storage: 0 })
const SITES = {
  key: 'k', saved_sites: 2, uri: 'https://x',
  sites: [
    summary(detail),
    { site: 'grafana.kellyferrone.com', origin: 'https://grafana.kellyferrone.com', saved: true, saved_at: Date.now() / 1000 - 3600, cookies: 5, local_storage: 12, session_storage: 0, secrets: [{ name: 'grafana', keys: ['token'] }] },
    { site: 'selenium.kellyferrone.com', origin: 'https://selenium.kellyferrone.com', saved: false, saved_at: null, cookies: 0, local_storage: 0, session_storage: 0, secrets: [{ name: 'sel', description: 'd', keys: ['a'] }] },
  ],
  details: {
    'the-internet.herokuapp.com': detail,
    'grafana.kellyferrone.com': { ...summary(detail), site: 'grafana.kellyferrone.com', cookies: [], local_storage: {}, own_cookies: [], kept_shared: [] },
    'selenium.kellyferrone.com': { site: 'selenium.kellyferrone.com', origin: 'https://selenium.kellyferrone.com', saved: false, saved_at: null, cookies: [], local_storage: {}, session_storage: {}, secrets: [], own_cookies: [], kept_shared: [] },
  },
}
const row = { key: 'k', name: 'mine', live: true, attached: true, session_id: 'b1', files_rev: 1, flows_rev: 1, files_count: 0, site_data_count: 2, site_data_rev: 'r1' }
const base = {
  'GET /admin/sessions/k/files': { body: { session: row, downloads: [], screenshots: [], files: [], browser: true } },
  'GET /admin/sessions/k/flows': { body: { enabled: true, flows: [], rev: 1, session: 'k' } },
}

function setup(routes = {}) {
  const net = fakeFetch({ ...base, 'GET /admin/sessions/k/site-data': { body: SITES }, ...routes })
  const live = new Live(api, '')
  const r = render(SessionDetail, { key: 'k', tab: 'site-data', flow: undefined, api, live, root: '' })
  return { ...r, ...net, live }
}
const sections = (c: HTMLElement) => [...c.querySelectorAll('#paneSiteData section.section')] as HTMLElement[]
const loaded = (c: HTMLElement) => vi.waitFor(() => expect(sections(c)).toHaveLength(3))

test('the subtab carries the count, and rows come in order with their counts', async () => {
  const { container } = setup()
  expect(screen.getByRole('tab', { name: /Site data/ })).toHaveAttribute('aria-selected', 'true')
  await loaded(container)
  expect(container.querySelector('#siteDataTotal')).toHaveTextContent('2')
  const [a, b, c] = sections(container)
  expect(a.querySelector('.title')).toHaveTextContent('https://the-internet.herokuapp.com')
  expect(a.querySelector('.head')).toHaveTextContent('saved 2m ago')
  expect(a.querySelector('.head')).toHaveTextContent('2 cookies · 2 local · 0 session · 1 secret')
  expect(b.querySelector('.head')).toHaveTextContent('saved 1h ago')
  expect(b.querySelector('.head')).toHaveTextContent('5 cookies · 12 local · 0 session · 1 secret')
  expect(c.querySelector('.head')).toHaveTextContent('nothing saved · 1 secret')
  expect(c.querySelector('.pill')).toBeNull()
})

test('first saved row is open, the others shut', async () => {
  const { container } = setup()
  await loaded(container)
  const [a, b, c] = sections(container)
  expect(a.querySelector('.title')).toHaveAttribute('aria-expanded', 'true')
  expect(b.querySelector('.title')).toHaveAttribute('aria-expanded', 'false')
  expect(c.querySelector('.title')).toHaveAttribute('aria-expanded', 'false')
})

test('the first saved row opens even when a secret-only row sorts before it', async () => {
  const [a, b, c] = SITES.sites
  const { container } = setup({ 'GET /admin/sessions/k/site-data': { body: { ...SITES, sites: [c, a, b] } } })
  await loaded(container)
  const [first, second, third] = sections(container)
  expect(first.querySelector('.title')).toHaveAttribute('aria-expanded', 'false')
  expect(second.querySelector('.title')).toHaveAttribute('aria-expanded', 'true')
  expect(third.querySelector('.title')).toHaveAttribute('aria-expanded', 'false')
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
  expect(a.getByText('Local storage')).toBeInTheDocument()
  expect(a.getByText('theme').closest('.line')).toHaveTextContent('dark')
  const session = a.getByText('Session storage').nextElementSibling!
  expect(session).toHaveTextContent('none')
})

test('the shared pill follows the payload: a site\'s own dotted cookie has none', async () => {
  const own = cookie('own', 'o', { domain: '.the-internet.herokuapp.com', shared: false })
  const d = { ...detail, cookies: [own, ...detail.cookies.slice(1)] }
  const { container } = setup({ 'GET /admin/sessions/k/site-data': { body: { ...SITES, details: { ...SITES.details, [detail.site]: d } } } })
  await loaded(container)
  const a = within(sections(container)[0])
  expect(a.getByText('own').closest('.line')!.querySelector('.pill.shared')).toBeNull()
  expect(a.getByText('optimizelyEndUserId').closest('.line')!.querySelector('.pill.shared')).not.toBeNull()
})

test('secret rows: name, description, one pill per key name', async () => {
  const { container } = setup()
  await loaded(container)
  const s = sections(container)[0].querySelector('.line.secret')!
  expect(s).toHaveTextContent('🔑 the-internet')
  expect(s).toHaveTextContent('Public demo login')
  expect([...s.querySelectorAll('.pill')].map((p) => p.textContent)).toEqual(['username', 'password'])
})

test('a secret-only row has no Forget, and says why it is listed', async () => {
  const { container } = setup()
  await loaded(container)
  const [a, b, c] = sections(container)
  expect(within(a).getByRole('button', { name: 'Forget' })).toBeInTheDocument()
  expect(within(b).getByRole('button', { name: 'Forget' })).toBeInTheDocument()
  expect(within(c).queryByRole('button', { name: 'Forget' })).toBeNull()
  await fireEvent.click(c.querySelector('.title')!)
  expect(c).toHaveTextContent('Nothing saved. It stays listed because a secret is allowed here.')
  expect(c.querySelector('.line.secret')).toHaveTextContent('sel')
})

test('the empty line shows only when nothing is saved', async () => {
  const none = { ...SITES, saved_sites: 0, sites: SITES.sites.map((s) => ({ ...s, saved: false, saved_at: null })) }
  const { container, unmount } = setup({ 'GET /admin/sessions/k/site-data': { body: none } })
  await loaded(container)
  const line = container.querySelector('#paneSiteData > p')!
  expect(line).toHaveTextContent('Nothing saved — an agent calls save_site_data after signing in.')
  expect(line.querySelector('code')).toHaveTextContent('save_site_data')
  unmount()
  const again = setup()
  await loaded(again.container)
  expect(again.container.querySelector('#paneSiteData > p')).toBeNull()
})

test('Forget confirms with goes and stays, then DELETEs the site and reloads', async () => {
  const { container, calls } = setup({ 'DELETE /admin/sessions/k/site-data/the-internet.herokuapp.com': { body: { forgotten: {} } } })
  await loaded(container)
  await fireEvent.click(within(sections(container)[0]).getByRole('button', { name: 'Forget' }))
  const dlg = document.querySelector('.modal')! as HTMLElement
  expect(dlg.querySelector('.head')).toHaveTextContent('Forget site data')
  expect(dlg).toHaveTextContent('https://the-internet.herokuapp.com — the next browser comes back signed out here; one open now keeps what it has.')
  const pairs = [...dlg.querySelectorAll('.pair')].map((p) => p.textContent)
  expect(pairs).toEqual([
    'goesrack.session · theme · tour-seen',
    'staysoptimizelyEndUserId, shared with .herokuapp.com',
    'staysthe-internet secret',
  ])
  expect(calls.filter((c) => c.method === 'DELETE')).toHaveLength(0)
  const gets = () => calls.filter((c) => c.method === 'GET' && c.path.endsWith('/site-data')).length
  const before = gets()
  await fireEvent.click(within(dlg).getByRole('button', { name: 'Forget' }))
  await vi.waitFor(() => expect(calls.filter((c) => c.method === 'DELETE').map((c) => c.path)).toEqual(['/admin/sessions/k/site-data/the-internet.herokuapp.com']))
  await vi.waitFor(() => expect(gets()).toBe(before + 1))
})

test('Cancel forgets nothing', async () => {
  const { container, calls } = setup()
  await loaded(container)
  await fireEvent.click(within(sections(container)[1]).getByRole('button', { name: 'Forget' }))
  await fireEvent.click(within(document.querySelector('.modal') as HTMLElement).getByRole('button', { name: 'Cancel' }))
  expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
})

test('a pushed site_data_rev change reloads; the same rev does not', async () => {
  const { container, calls, live } = setup()
  await loaded(container)
  const gets = () => calls.filter((c) => c.method === 'GET' && c.path.endsWith('/site-data')).length
  expect(gets()).toBe(1)
  const push = (rev: string) => { live.data = { sessions: [{ ...row, site_data_rev: rev }] } }
  push('r1')
  await new Promise((r) => setTimeout(r, 10))
  expect(gets()).toBe(1)
  push('r2')
  await vi.waitFor(() => expect(gets()).toBe(2))
})

test('a failed load is retried by the next push at the same rev', async () => {
  let n = 0
  const { container, live } = setup({
    'GET /admin/sessions/k/site-data': () => (++n <= 2 ? { status: 500, body: { error: 'flaky' } } : { body: SITES }),
  })
  await vi.waitFor(() => expect(screen.getByText('flaky')).toBeInTheDocument())
  live.data = { sessions: [{ ...row, site_data_rev: 'r1' }] }
  await vi.waitFor(() => expect(n).toBe(2))
  await new Promise((r) => setTimeout(r, 10))
  live.data = { sessions: [{ ...row, site_data_rev: 'r1', files_count: 1 }] }
  await loaded(container)
  expect(n).toBe(3)
})

test('an error shows in the pane, Loading… before', async () => {
  const d = deferred<{ status: number; body: unknown }>()
  const { container } = setup({ 'GET /admin/sessions/k/site-data': () => d.promise })
  expect(within(container.querySelector('#paneSiteData') as HTMLElement).getByText('Loading…')).toBeInTheDocument()
  d.resolve({ status: 500, body: { error: 'nope' } })
  await vi.waitFor(() => expect(screen.getByText('nope')).toHaveClass('error'))
})

// A parent-only row: example.com exists only because of `.example.com`, which
// is its own, so Forget takes it. Beneath it, a row listed for a secret whose
// only cookie is that parent's has nothing of its own to forget.
const PARENT = cookie('shared', 's', { domain: '.example.com', shared: true })
const PARENTS = {
  key: 'k', saved_sites: 1,
  sites: [
    { site: 'app.example.com', origin: null, saved: false, saved_at: null, cookies: 1, local_storage: 0, session_storage: 0, secrets: [{ name: 'app', keys: ['a'] }] },
    { site: 'example.com', origin: null, saved: true, saved_at: Date.now() / 1000 - 60, cookies: 1, local_storage: 0, session_storage: 0, secrets: [] },
  ],
  details: {
    'app.example.com': { site: 'app.example.com', origin: null, saved: false, saved_at: null, cookies: [PARENT], local_storage: {}, session_storage: {}, secrets: [{ name: 'app', keys: ['a'] }], own_cookies: [], kept_shared: ['shared'] },
    'example.com': { site: 'example.com', origin: null, saved: true, saved_at: Date.now() / 1000 - 60, cookies: [PARENT], local_storage: {}, session_storage: {}, secrets: [], own_cookies: ['shared'], kept_shared: [] },
  },
}

test('a parent-only row: its dotted cookie goes, and the row under it has no Forget', async () => {
  const { container } = setup({ 'GET /admin/sessions/k/site-data': { body: PARENTS } })
  await vi.waitFor(() => expect(sections(container)).toHaveLength(2))
  const [child, parent] = sections(container)
  expect(within(child).queryByRole('button', { name: 'Forget' })).toBeNull()
  await fireEvent.click(within(parent).getByRole('button', { name: 'Forget' }))
  const pairs = [...document.querySelectorAll('.modal .pair')].map((p) => p.textContent)
  expect(pairs).toEqual(['goesshared'])
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
