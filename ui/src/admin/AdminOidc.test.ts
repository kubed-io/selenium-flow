import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { FakeEventSource, fakeFetch, fakeJwt } from '../test/helpers'
import Admin from './Admin.svelte'
import * as oidc from './oidc'

// Only the navigation is faked: everything else in oidc.ts runs for real.
vi.mock('./oidc', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./oidc')>()),
  begin: vi.fn(async () => {}),
}))

const ISSUER = 'https://auth.example.com/realms/example'
const CONFIG = JSON.stringify({ issuer: ISSUER, client_id: 'selenium-flow-admin' })
const DISCOVERY = {
  issuer: ISSUER,
  authorization_endpoint: ISSUER + '/protocol/openid-connect/auth',
  token_endpoint: ISSUER + '/protocol/openid-connect/token',
}
const WELL_KNOWN = 'GET /realms/example/.well-known/openid-configuration'
const TOKEN = 'POST /realms/example/protocol/openid-connect/token'
const WORKSPACES = { workspaces: [{ key: 'k1', name: 'claudecode', live: true }], events_url: '/e' }
const ACCESS = fakeJwt({ preferred_username: 'drk' })

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear()
  history.replaceState(null, '', '/#/')
  vi.stubGlobal('EventSource', FakeEventSource)
  vi.mocked(oidc.begin).mockClear()
})

afterEach(() => { vi.useRealTimers() })

function returning(query = 'code=c1&state=s1', silent = false) {
  sessionStorage.setItem(oidc.PENDING, JSON.stringify({ state: 's1', verifier: 'v1', hash: '#/', silent }))
  history.replaceState(null, '', '/?' + query)
}

const issuerAnd = (workspaces: { status?: number; body?: unknown }) => fakeFetch({
  [WELL_KNOWN]: { body: DISCOVERY },
  [TOKEN]: { body: { access_token: ACCESS, refresh_token: 'r1', expires_in: 300 } },
  'GET /admin/workspaces': workspaces,
})

const stored = (): string => Array.from({ length: sessionStorage.length }, (_, i) => sessionStorage.getItem(sessionStorage.key(i)!)).join('|')

test("without an OIDC config the card is today's", () => {
  fakeFetch({})
  const { container } = render(Admin, { mount: '', console: '/grid' })
  expect(container.querySelector('#oidcSignIn')).toBeNull()
})

test('with one, the button starts a sign-in', async () => {
  fakeFetch({})
  const { container } = render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await fireEvent.click(container.querySelector('#oidcSignIn')!)
  expect(oidc.begin).toHaveBeenCalledWith({ issuer: ISSUER, client_id: 'selenium-flow-admin' }, false)
})

test('a reply signs in with the access token and stores no token (ruling 2)', async () => {
  returning()
  const { calls } = issuerAnd({ body: WORKSPACES })
  render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await vi.waitFor(() => expect(screen.getByText('Sign out')).toBeInTheDocument())
  expect(calls.find((c) => c.path === '/admin/workspaces')!.headers.get('Authorization')).toBe('Bearer ' + ACCESS)
  expect(sessionStorage.getItem('sf-token')).toBeNull()
  expect(sessionStorage.getItem(oidc.MARKER)).toBe('oidc')
  expect(stored()).not.toContain(ACCESS)
  expect(stored()).not.toContain('r1')
  expect(localStorage.length).toBe(0)
})

test('a sign-in without the admin role is told so, and keeps nothing', async () => {
  returning()
  issuerAnd({ status: 403, body: { error: 'this sign-in does not hold an admin role' } })
  const { container } = render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await vi.waitFor(() => expect(screen.getByText('Signed in as drk, who does not hold an admin role.')).toBeVisible())
  expect(container.querySelector('#login')).toBeVisible()
  expect(sessionStorage.getItem(oidc.MARKER)).toBeNull()
})

test('a sign-in the server refuses says so', async () => {
  returning()
  issuerAnd({ status: 401 })
  render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await vi.waitFor(() => expect(screen.getByText('The server refused this sign-in.')).toBeVisible())
})

test('a reload with the marker signs in again silently, once', async () => {
  sessionStorage.setItem(oidc.MARKER, 'oidc')
  fakeFetch({})
  render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await vi.waitFor(() => expect(oidc.begin).toHaveBeenCalledWith(expect.objectContaining({ issuer: ISSUER }), true))
  expect(sessionStorage.getItem(oidc.MARKER)).toBeNull()
})

test('a silent miss lands on the card with nothing to say', async () => {
  returning('error=login_required&state=s1', true)
  fakeFetch({})
  const { container } = render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await vi.waitFor(() => expect(container.querySelector('#login')).toBeVisible())
  expect(container.querySelector('#loginError')).not.toBeVisible()
})

test('Sign out forgets the OIDC sign-in', async () => {
  returning()
  issuerAnd({ body: WORKSPACES })
  const { container } = render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await vi.waitFor(() => expect(screen.getByText('Sign out')).toBeInTheDocument())
  await fireEvent.click(screen.getByText('Sign out'))
  expect(container.querySelector('#login')).toBeVisible()
  expect(sessionStorage.getItem(oidc.MARKER)).toBeNull()
  expect(container.querySelector('#loginError')).not.toBeVisible()
})

// --- what Task 7's review carried forward --------------------------------

test('the code leaves the URL before a stored token is probed, and the reply wins', async () => {
  sessionStorage.setItem('sf-token', 'old')
  returning()
  const { calls, fn } = issuerAnd({ body: WORKSPACES })
  const seen: string[] = []
  const reply = fn.getMockImplementation()!
  fn.mockImplementation(async (url, init) => { seen.push(location.search); return reply(url, init) })
  render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  expect(location.search).toBe('')
  expect(calls.filter((c) => c.path === '/admin/workspaces')).toHaveLength(0)
  await vi.waitFor(() => expect(screen.getByText('Sign out')).toBeInTheDocument())
  expect(seen.length).toBeGreaterThan(0)
  expect(seen.every((s) => s === '')).toBe(true)
  const probes = calls.filter((c) => c.path === '/admin/workspaces')
  expect(probes.map((c) => c.headers.get('Authorization'))).not.toContain('Bearer old')
  expect(probes[0].headers.get('Authorization')).toBe('Bearer ' + ACCESS)
})

test("the issuer's words are shown as text, never as markup", async () => {
  returning('error=access_denied&error_description=%3Cb%3Eno%3C%2Fb%3E&state=s1')
  fakeFetch({})
  const { container } = render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await vi.waitFor(() => expect(screen.getByText('The issuer said: <b>no</b>.')).toBeVisible())
  expect(container.querySelector('#loginError b')).toBeNull()
})

test('an issuer down on the button says it could not be reached', async () => {
  vi.mocked(oidc.begin).mockRejectedValueOnce(new Error(oidc.NOT_REACHED))
  fakeFetch({})
  const { container } = render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await fireEvent.click(container.querySelector('#oidcSignIn')!)
  await vi.waitFor(() => expect(screen.getByText('The issuer could not be reached.')).toBeVisible())
})

/* A token endpoint that grants the code, and answers a refresh with `renewal`. */
function renewing(renewal: { status?: number; body?: unknown }) {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  returning()
  return fakeFetch({
    [WELL_KNOWN]: { body: DISCOVERY },
    [TOKEN]: (init) => String(init.body).includes('grant_type=refresh_token')
      ? renewal
      : { body: { access_token: ACCESS, refresh_token: 'r1', expires_in: 300 } },
    'GET /admin/workspaces': { body: WORKSPACES },
    'GET /admin/settings': { body: { sections: [] } },
  })
}
const refreshes = (calls: { method: string; path: string; body?: unknown }[]) =>
  calls.filter((c) => c.method === 'POST' && String(c.body).includes('grant_type=refresh_token'))

test('a renewal 30 s before expiry replaces the access token', async () => {
  const RENEWED = fakeJwt({ preferred_username: 'drk', n: 2 })
  const { calls } = renewing({ body: { access_token: RENEWED, expires_in: 300 } })
  render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await vi.waitFor(() => expect(screen.getByText('Sign out')).toBeInTheDocument())
  await vi.advanceTimersByTimeAsync(269_000)
  expect(refreshes(calls)).toHaveLength(0)
  await vi.advanceTimersByTimeAsync(2_000)
  expect(refreshes(calls)).toHaveLength(1)
  history.replaceState(null, '', '/#/settings'); window.dispatchEvent(new HashChangeEvent('hashchange'))
  await vi.waitFor(() => expect(calls.find((c) => c.path === '/admin/settings')).toBeTruthy())
  expect(calls.find((c) => c.path === '/admin/settings')!.headers.get('Authorization')).toBe('Bearer ' + RENEWED)
  expect(stored()).not.toContain(RENEWED)
})

test('a refused renewal signs out with exactly "Your sign-in ended; sign in again."', async () => {
  renewing({ status: 400, body: { error: 'invalid_grant' } })
  const { container } = render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await vi.waitFor(() => expect(screen.getByText('Sign out')).toBeInTheDocument())
  await vi.advanceTimersByTimeAsync(271_000)
  await vi.waitFor(() => expect(container.querySelector('#loginError')).toHaveTextContent(/^Your sign-in ended; sign in again\.$/))
  expect(container.querySelector('#login')).toBeVisible()
  expect(sessionStorage.getItem(oidc.MARKER)).toBeNull()
})

test('a 401 mid-session signs out and stops the renewal', async () => {
  const { calls } = renewing({ body: { access_token: ACCESS, expires_in: 300 } })
  const { container } = render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await vi.waitFor(() => expect(screen.getByText('Sign out')).toBeInTheDocument())
  fakeFetch({ 'GET /admin/settings': { status: 401 } })
  history.replaceState(null, '', '/#/settings'); window.dispatchEvent(new HashChangeEvent('hashchange'))
  await vi.waitFor(() => expect(container.querySelector('#login')).toBeVisible())
  expect(sessionStorage.getItem(oidc.MARKER)).toBeNull()
  await vi.advanceTimersByTimeAsync(600_000)
  expect(refreshes(calls)).toHaveLength(0)
})

test('Sign out and unmounting both stop the renewal', async () => {
  const { calls } = renewing({ body: { access_token: ACCESS, expires_in: 300 } })
  render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await vi.waitFor(() => expect(screen.getByText('Sign out')).toBeInTheDocument())
  await fireEvent.click(screen.getByText('Sign out'))
  await vi.advanceTimersByTimeAsync(600_000)
  expect(refreshes(calls)).toHaveLength(0)

  returning()
  const second = render(Admin, { mount: '', console: '/grid', oidc: CONFIG })
  await vi.waitFor(() => expect(second.getByText('Sign out')).toBeInTheDocument())
  second.unmount()
  await vi.advanceTimersByTimeAsync(600_000)
  expect(refreshes(calls)).toHaveLength(0)
})
