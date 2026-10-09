import { beforeEach, expect, test, vi } from 'vitest'
import { fakeFetch, fakeJwt } from '../test/helpers'
import {
  begin, challengeOf, complete, NOT_COMPLETED, NOT_OURS, NOT_REACHED, OTHER_ISSUER,
  PENDING, readConfig, refresh, renewIn, usernameOf,
} from './oidc'

const ISSUER = 'https://auth.example.com/realms/example'
const CONFIG = { issuer: ISSUER, client_id: 'selenium-flow-admin' }
const DISCOVERY = {
  issuer: ISSUER,
  authorization_endpoint: ISSUER + '/protocol/openid-connect/auth',
  token_endpoint: ISSUER + '/protocol/openid-connect/token',
}
const WELL_KNOWN = 'GET /realms/example/.well-known/openid-configuration'
const TOKEN = 'POST /realms/example/protocol/openid-connect/token'

beforeEach(() => {
  sessionStorage.clear()
  history.replaceState(null, '', '/flow/#/')
})

function replyWith(query: string, pending: object | null = { state: 's1', verifier: 'v1', hash: '#/w/desk', silent: false }) {
  if (pending) sessionStorage.setItem(PENDING, JSON.stringify(pending))
  history.replaceState(null, '', '/flow/?' + query)
}

test('readConfig takes the two public values and nothing malformed', () => {
  expect(readConfig('')).toBeNull()
  expect(readConfig(undefined)).toBeNull()
  expect(readConfig('{nope')).toBeNull()
  expect(readConfig('{"issuer":"x"}')).toBeNull()
  expect(readConfig(JSON.stringify(CONFIG))).toEqual(CONFIG)
})

test('the S256 challenge is RFC 7636 Appendix B', async () => {
  expect(await challengeOf('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk')).toBe('E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM')
})

test('begin keeps the verifier and asks for a code for the page itself', async () => {
  fakeFetch({ [WELL_KNOWN]: { body: DISCOVERY } })
  const go = vi.fn()
  await begin(CONFIG, false, go)
  const url = new URL(go.mock.calls[0][0])
  const pending = JSON.parse(sessionStorage.getItem(PENDING)!)
  expect(url.origin + url.pathname).toBe(DISCOVERY.authorization_endpoint)
  expect(Object.fromEntries(url.searchParams)).toEqual({
    response_type: 'code',
    client_id: 'selenium-flow-admin',
    redirect_uri: location.origin + '/flow/',
    scope: 'openid',
    state: pending.state,
    code_challenge: await challengeOf(pending.verifier),
    code_challenge_method: 'S256',
  })
  expect(pending).toMatchObject({ hash: '#/', silent: false })
  expect(pending.verifier.length).toBeGreaterThanOrEqual(43)
})

test('a silent begin asks for no prompt', async () => {
  fakeFetch({ [WELL_KNOWN]: { body: DISCOVERY } })
  const go = vi.fn()
  await begin(CONFIG, true, go)
  expect(new URL(go.mock.calls[0][0]).searchParams.get('prompt')).toBe('none')
})

test('a discovery document naming another issuer is refused', async () => {
  fakeFetch({ [WELL_KNOWN]: { body: { ...DISCOVERY, issuer: 'https://evil.example.com' } } })
  const go = vi.fn()
  await expect(begin(CONFIG, false, go)).rejects.toThrow(NOT_REACHED)
  expect(go).not.toHaveBeenCalled()
})

test('an endpoint that is not http(s) is never followed nor sent the code', async () => {
  for (const bad of ['javascript:alert(document.domain)//', 'data:text/html,hi', 'not a url']) {
    for (const field of ['authorization_endpoint', 'token_endpoint']) {
      const { calls } = fakeFetch({ [WELL_KNOWN]: { body: { ...DISCOVERY, [field]: bad } } })
      const go = vi.fn()
      await expect(begin(CONFIG, false, go)).rejects.toThrow(NOT_REACHED)
      expect(go).not.toHaveBeenCalled()
      replyWith('code=c1&state=s1')
      expect(await complete(CONFIG)).toEqual({ kind: 'error', message: NOT_REACHED })
      expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0)
    }
  }
})

test('no reply is nothing to do', async () => {
  expect(await complete(CONFIG)).toEqual({ kind: 'none' })
})

test('a reply exchanges its code, cleans the URL and keeps nothing behind', async () => {
  const { calls } = fakeFetch({
    [WELL_KNOWN]: { body: DISCOVERY },
    [TOKEN]: { body: { access_token: 'a1', refresh_token: 'r1', expires_in: 300 } },
  })
  replyWith('code=c1&state=s1&iss=' + encodeURIComponent(ISSUER))
  const before = Date.now()
  const reply = await complete(CONFIG)
  expect(reply).toMatchObject({ kind: 'tokens', tokens: { access: 'a1', refresh: 'r1' } })
  if (reply.kind === 'tokens') expect(reply.tokens.expiresAt).toBeGreaterThanOrEqual(before + 300_000)
  expect(location.search).toBe('')
  expect(location.hash).toBe('#/w/desk')
  expect(sessionStorage.getItem(PENDING)).toBeNull()
  const post = calls.find((c) => c.method === 'POST')!
  expect(Object.fromEntries(new URLSearchParams(post.body as string))).toEqual({
    grant_type: 'authorization_code',
    code: 'c1',
    redirect_uri: location.origin + '/flow/',
    code_verifier: 'v1',
    client_id: 'selenium-flow-admin',
  })
})

test('a reply whose state is not ours is refused, and its code still leaves the URL', async () => {
  fakeFetch({})
  replyWith('code=c1&state=forged')
  expect(await complete(CONFIG)).toEqual({ kind: 'error', message: NOT_OURS })
  expect(location.search).toBe('')
})

test('a reply with no sign-in pending is refused', async () => {
  fakeFetch({})
  replyWith('code=c1&state=s1', null)
  expect(await complete(CONFIG)).toEqual({ kind: 'error', message: NOT_OURS })
})

test('a reply from another issuer is refused', async () => {
  fakeFetch({})
  replyWith('code=c1&state=s1&iss=' + encodeURIComponent('https://evil.example.com'))
  expect(await complete(CONFIG)).toEqual({ kind: 'error', message: OTHER_ISSUER })
})

test("an issuer's error is said; a silent miss is quiet", async () => {
  fakeFetch({})
  replyWith('error=access_denied&error_description=Cancelled&state=s1')
  expect(await complete(CONFIG)).toEqual({ kind: 'error', message: 'The issuer said: Cancelled.' })
  replyWith('error=login_required&state=s1', { state: 's1', verifier: 'v1', hash: '#/', silent: true })
  expect(await complete(CONFIG)).toEqual({ kind: 'quiet' })
})

test('a refused exchange says so', async () => {
  fakeFetch({ [WELL_KNOWN]: { body: DISCOVERY }, [TOKEN]: { status: 400, body: { error: 'invalid_grant' } } })
  replyWith('code=c1&state=s1')
  expect(await complete(CONFIG)).toEqual({ kind: 'error', message: NOT_COMPLETED })
})

test('refresh swaps the tokens and keeps a refresh token the issuer did not reissue', async () => {
  const { calls } = fakeFetch({ [WELL_KNOWN]: { body: DISCOVERY }, [TOKEN]: { body: { access_token: 'a2', expires_in: 300 } } })
  const next = await refresh(CONFIG, { access: 'a1', refresh: 'r1', expiresAt: 0 })
  expect(next).toMatchObject({ access: 'a2', refresh: 'r1' })
  const post = calls.find((c) => c.method === 'POST')!
  expect(Object.fromEntries(new URLSearchParams(post.body as string))).toEqual({
    grant_type: 'refresh_token', refresh_token: 'r1', client_id: 'selenium-flow-admin',
  })
})

test('a refused refresh throws', async () => {
  fakeFetch({ [WELL_KNOWN]: { body: DISCOVERY }, [TOKEN]: { status: 400 } })
  await expect(refresh(CONFIG, { access: 'a1', refresh: 'r1', expiresAt: 0 })).rejects.toThrow()
})

test('renewal is 30 s before expiry, never sooner than 5 s', () => {
  expect(renewIn({ access: 'a', expiresAt: 300_000 }, 0)).toBe(270_000)
  expect(renewIn({ access: 'a', expiresAt: 10_000 }, 0)).toBe(5_000)
  // Past 2^31 - 1 ms a browser fires setTimeout at once: a renewal loop.
  expect(renewIn({ access: 'a', expiresAt: 30 * 86_400_000 }, 0)).toBe(2 ** 31 - 1)
})

test('usernameOf reads preferred_username, for display only', () => {
  expect(usernameOf(fakeJwt({ preferred_username: 'drk' }))).toBe('drk')
  expect(usernameOf(fakeJwt({ preferred_username: 'zoë' }))).toBe('zoë')
  expect(usernameOf('not-a-jwt')).toBeUndefined()
})

// What a browser hands back when the issuer is down, CORS refuses, or a proxy
// answers with a page: never a rejection the caller did not ask for.
function issuerAnswers(token: () => Response, discovery: () => Response = () => Response.json(DISCOVERY)) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => (url.endsWith('/openid-configuration') ? discovery() : token())))
}
const unreachable = () => { throw new TypeError('Failed to fetch') }
const html = () => new Response('<html>Bad gateway</html>', { status: 200 })

test('a discovery that cannot be fetched or read is not reached', async () => {
  const go = vi.fn()
  for (const discovery of [unreachable, html, () => new Response('null'), () => new Response('', { status: 502 })]) {
    issuerAnswers(html, discovery)
    await expect(begin(CONFIG, false, go)).rejects.toThrow(NOT_REACHED)
    replyWith('code=c1&state=s1')
    expect(await complete(CONFIG)).toEqual({ kind: 'error', message: NOT_REACHED })
  }
  expect(go).not.toHaveBeenCalled()
  expect(sessionStorage.getItem(PENDING)).toBeNull()
})

test('a token endpoint that cannot be reached or read does not complete the sign-in', async () => {
  for (const token of [unreachable, html, () => Response.json(null), () => Response.json({ access_token: 7 })]) {
    issuerAnswers(token)
    replyWith('code=c1&state=s1')
    expect(await complete(CONFIG)).toEqual({ kind: 'error', message: NOT_COMPLETED })
  }
  issuerAnswers(html)
  await expect(refresh(CONFIG, { access: 'a1', refresh: 'r1', expiresAt: 0 })).rejects.toThrow()
})

test('the code leaves the address bar before anything is awaited', async () => {
  fakeFetch({ [WELL_KNOWN]: () => new Promise(() => {}) })
  replyWith('code=c1&state=s1')
  void complete(CONFIG)
  expect(location.search).toBe('')
  expect(location.hash).toBe('#/w/desk')
  expect(sessionStorage.getItem(PENDING)).toBeNull()
})

test('only a hash is ever restored', async () => {
  fakeFetch({})
  replyWith('error=access_denied&state=s1', { state: 's1', verifier: 'v1', hash: '?next=https://evil.example.com', silent: false })
  await complete(CONFIG)
  expect(location.pathname).toBe('/flow/')
  expect(location.search).toBe('')
  expect(location.hash).toBe('')
})

test('no token, code or verifier is kept in storage or logged', async () => {
  const logged = (['log', 'info', 'warn', 'error', 'debug'] as const).map((m) => vi.spyOn(console, m))
  fakeFetch({
    [WELL_KNOWN]: { body: DISCOVERY },
    [TOKEN]: { body: { access_token: 'a1', refresh_token: 'r1', expires_in: 300 } },
  })
  await begin(CONFIG, false, vi.fn())
  expect(Object.keys(sessionStorage)).toEqual([PENDING])
  const { state } = JSON.parse(sessionStorage.getItem(PENDING)!)
  history.replaceState(null, '', `/flow/?code=c1&state=${state}`)
  expect(await complete(CONFIG)).toMatchObject({ kind: 'tokens' })
  await refresh(CONFIG, { access: 'a1', refresh: 'r1', expiresAt: 0 })
  expect(sessionStorage.length).toBe(0)
  expect(localStorage.length).toBe(0)
  for (const spy of logged) expect(spy).not.toHaveBeenCalled()
})

test('an expires_in that is not a number still renews', async () => {
  fakeFetch({ [WELL_KNOWN]: { body: DISCOVERY }, [TOKEN]: { body: { access_token: 'a2', expires_in: 'soon' } } })
  const next = await refresh(CONFIG, { access: 'a1', refresh: 'r1', expiresAt: 0 })
  expect(Number.isFinite(renewIn(next))).toBe(true)
})
