import { vi } from 'vitest'

export interface Reply { status?: number; body?: unknown }
type Route = Reply | ((init: RequestInit) => Reply | Promise<Reply>)

export function deferred<T = void>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((a, b) => { resolve = a; reject = b })
  return { promise, resolve, reject }
}

/** A fetch that answers `"METHOD /path"`; anything else is a 404 with an error body. */
export function fakeFetch(routes: Record<string, Route>) {
  const calls: { method: string; path: string; body?: unknown; headers: Headers; signal?: AbortSignal }[] = []
  const fn = vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = (init.method ?? 'GET').toUpperCase()
    const path = new URL(url, 'http://test').pathname
    calls.push({
      method, path, headers: new Headers(init.headers),
      body: init.body ? parsed(init.body) : undefined,
      signal: init.signal ?? undefined,
    })
    // Keyed on path only; a query string in `url` is dropped and never matches.
    const route = routes[`${method} ${path}`]
    const reply = typeof route === 'function' ? await route(init) : route
    if (init.signal?.aborted) throw new DOMException('aborted', 'AbortError')
    const r = reply ?? { status: 404, body: { error: `no route ${method} ${path}` } }
    const status = r.status ?? 200
    // Real fetch: these statuses forbid a body, and the Response constructor
    // throws if given one.
    const empty = status === 204 || status === 205 || status === 304
    return new Response(empty ? null : JSON.stringify(r.body ?? {}), {
      status, headers: empty ? undefined : { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fn)
  return { fn, calls }
}

/** A JSON body as its value; anything else — a form — as its text. */
function parsed(body: BodyInit): unknown {
  const text = String(body)
  try { return JSON.parse(text) } catch { return text }
}

/** An unsigned JWT-shaped string: enough for code that only reads the claims.
    UTF-8 first, so a username like `zoë` survives `btoa`. */
export function fakeJwt(claims: object): string {
  const part = (o: object) => btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(o))))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  return `${part({ alg: 'none' })}.${part(claims)}.sig`
}

export class FakeEventSource {
  static last: FakeEventSource | null = null
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor(public url: string) { FakeEventSource.last = this }
  close() { this.closed = true }
  emit(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) }) }
}
