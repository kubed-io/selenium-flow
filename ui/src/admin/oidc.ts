/* Sign in with OIDC: Authorization Code + PKCE, run by the page itself (spec
   2026-10-09-admin-oidc). The page talks to the issuer — discovery, the
   redirect, the code exchange, the renewals — and hands the server nothing but
   the access token, as a bearer. Tokens live in memory; sessionStorage holds
   only what must survive the redirect, and only until it comes back. */

export interface OidcConfig { issuer: string; client_id: string }
export interface Tokens { access: string; refresh?: string; expiresAt: number }
export type Reply =
  | { kind: 'none' }
  | { kind: 'quiet' }
  | { kind: 'error'; message: string }
  | { kind: 'tokens'; tokens: Tokens }

interface Endpoints { authorization_endpoint: string; token_endpoint: string }
interface Pending { state: string; verifier: string; hash: string; silent: boolean }

export const PENDING = 'sf-oidc-pending'
export const MARKER = 'sf-signin'

export const NOT_REACHED = 'The issuer could not be reached.'
export const NOT_OURS = 'That sign-in reply was not ours; try again.'
export const OTHER_ISSUER = 'That sign-in reply came from another issuer.'
export const NOT_COMPLETED = 'The issuer would not complete the sign-in.'

// What a `prompt=none` attempt answers when the issuer has no session: show
// the card, say nothing.
const SILENT_MISSES = new Set(['login_required', 'interaction_required', 'consent_required', 'account_selection_required'])

export function readConfig(raw: string | undefined): OidcConfig | null {
  if (!raw) return null
  try {
    const c = JSON.parse(raw) as Partial<OidcConfig>
    return typeof c.issuer === 'string' && typeof c.client_id === 'string'
      ? { issuer: c.issuer, client_id: c.client_id }
      : null
  } catch {
    return null
  }
}

export function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

const random = (n: number): string => base64url(crypto.getRandomValues(new Uint8Array(n)))

export async function challengeOf(verifier: string): Promise<string> {
  return base64url(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))))
}

/* The page's own URL — the mount root — which is what the operator registers. */
export const redirectUri = (): string => location.origin + location.pathname.replace(/\/*$/, '/')

export const hasReply = (): boolean => {
  const params = new URLSearchParams(location.search)
  return params.has('code') || params.has('error')
}

/* The issuer's own no to a grant: a 4xx, RFC 6749 5.2's 400 `invalid_grant`
   (a spent refresh token, an ended SSO session) the usual one. Asking again
   changes nothing, unlike a network that is not back yet, an issuer answering
   5xx, or a 408 or 429, which say themselves to ask again later. */
export class Refused extends Error {}

/* Only a web URL is ever followed or sent the code: a hostile issuer's
   `javascript:` endpoint would otherwise run in this page's origin. And an
   https issuer's endpoints are https: the code and the tokens never cross in
   the clear because one line of its discovery said so. */
function web(url: unknown, secure: boolean): url is string {
  if (typeof url !== 'string') return false
  try {
    const { protocol } = new URL(url)
    return protocol === 'https:' || (!secure && protocol === 'http:')
  } catch {
    return false
  }
}

/* Every way discovery can fail — down, refused by CORS, a proxy's HTML, the
   wrong issuer — is one message: the issuer could not be reached. */
export async function discover(config: OidcConfig): Promise<Endpoints> {
  let doc: Partial<Endpoints> & { issuer?: unknown }
  try {
    const res = await fetch(config.issuer.replace(/\/+$/, '') + '/.well-known/openid-configuration')
    if (!res.ok) throw new Error(NOT_REACHED)
    doc = ((await res.json()) ?? {}) as typeof doc
  } catch {
    throw new Error(NOT_REACHED)
  }
  // OIDC Discovery 4.3: the document must name the issuer it was fetched for.
  const secure = config.issuer.startsWith('https:')
  if (doc.issuer !== config.issuer || !web(doc.authorization_endpoint, secure) || !web(doc.token_endpoint, secure)) {
    throw new Error(NOT_REACHED)
  }
  return { authorization_endpoint: doc.authorization_endpoint, token_endpoint: doc.token_endpoint }
}

export async function begin(
  config: OidcConfig,
  silent: boolean,
  go: (url: string) => void = (url) => location.assign(url),
): Promise<void> {
  const { authorization_endpoint } = await discover(config)
  const pending: Pending = { state: random(16), verifier: random(32), hash: location.hash, silent }
  sessionStorage.setItem(PENDING, JSON.stringify(pending))
  const url = new URL(authorization_endpoint)
  const params: Record<string, string> = {
    response_type: 'code',
    client_id: config.client_id,
    redirect_uri: redirectUri(),
    scope: 'openid',
    state: pending.state,
    code_challenge: await challengeOf(pending.verifier),
    code_challenge_method: 'S256',
  }
  if (silent) params.prompt = 'none'
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v)
  go(url.toString())
}

export async function complete(config: OidcConfig): Promise<Reply> {
  if (!hasReply()) return { kind: 'none' }
  const params = new URLSearchParams(location.search)
  let pending: Pending | null = null
  try { pending = JSON.parse(sessionStorage.getItem(PENDING) ?? 'null') as Pending | null } catch { /* not ours */ }
  sessionStorage.removeItem(PENDING)
  // The code leaves the address bar, and history, before anything else runs —
  // and what comes back is only ever a hash (spec §10, open redirect).
  const hash = typeof pending?.hash === 'string' && pending.hash.startsWith('#') ? pending.hash : location.hash
  history.replaceState(null, '', location.pathname + hash)
  if (!pending || params.get('state') !== pending.state) return { kind: 'error', message: NOT_OURS }
  const iss = params.get('iss')
  if (iss !== null && iss !== config.issuer) return { kind: 'error', message: OTHER_ISSUER }
  const error = params.get('error')
  if (error) {
    if (pending.silent && SILENT_MISSES.has(error)) return { kind: 'quiet' }
    return { kind: 'error', message: `The issuer said: ${params.get('error_description') || error}.` }
  }
  let endpoints: Endpoints
  try { endpoints = await discover(config) } catch { return { kind: 'error', message: NOT_REACHED } }
  try {
    const tokens = await grant(config, endpoints.token_endpoint, {
      grant_type: 'authorization_code',
      code: params.get('code') ?? '',
      redirect_uri: redirectUri(),
      code_verifier: pending.verifier,
    })
    return { kind: 'tokens', tokens }
  } catch {
    return { kind: 'error', message: NOT_COMPLETED }
  }
}

/* Throws `Refused` when renewing cannot work, and anything else when the
   issuer could not be asked or could not answer — worth asking again. */
export async function refresh(config: OidcConfig, tokens: Tokens): Promise<Tokens> {
  if (!tokens.refresh) throw new Refused('no refresh token')
  const { token_endpoint } = await discover(config)
  return grant(config, token_endpoint, { grant_type: 'refresh_token', refresh_token: tokens.refresh }, tokens)
}

/* When to renew: 30 s before the access token runs out, never sooner than 5 s,
   and never past setTimeout's 2^31 - 1 ms, beyond which a browser fires at once. */
export const renewIn = (tokens: Tokens, now = Date.now()): number =>
  Math.min(2 ** 31 - 1, Math.max(5_000, tokens.expiresAt - now - 30_000))

/* The access token's username, for a message. Display only: the server verified it. */
export function usernameOf(access: string): string | undefined {
  try {
    const b64 = access.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    const bytes = Uint8Array.from(atob(b64.padEnd(Math.ceil(b64.length / 4) * 4, '=')), (c) => c.charCodeAt(0))
    const claims = JSON.parse(new TextDecoder().decode(bytes)) as { preferred_username?: unknown }
    return typeof claims.preferred_username === 'string' ? claims.preferred_username : undefined
  } catch {
    return undefined
  }
}

async function grant(config: OidcConfig, endpoint: string, form: Record<string, string>, previous?: Tokens): Promise<Tokens> {
  // A fetch that throws (offline, CORS) throws on through: not a refusal.
  const res = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ ...form, client_id: config.client_id }),
  })
  if (res.status >= 400 && res.status < 500 && res.status !== 408 && res.status !== 429) throw new Refused(`the issuer refused the grant (${res.status})`)
  if (!res.ok) throw new Error(`the token endpoint answered ${res.status}`)
  let body: { access_token?: unknown; refresh_token?: unknown; expires_in?: unknown }
  try {
    body = ((await res.json()) ?? {}) as typeof body
  } catch {
    throw new Error('the token endpoint did not answer JSON')
  }
  if (typeof body.access_token !== 'string' || !body.access_token) throw new Error('the token endpoint sent no access token')
  // RFC 6749 5.1 makes `expires_in` a number; one that is not still renews.
  const lifetime = Number(body.expires_in)
  return {
    access: body.access_token,
    refresh: typeof body.refresh_token === 'string' ? body.refresh_token : previous?.refresh,
    expiresAt: Date.now() + (Number.isFinite(lifetime) && lifetime > 0 ? lifetime : 300) * 1000,
  }
}
