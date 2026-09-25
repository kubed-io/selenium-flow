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
      body: init.body ? JSON.parse(String(init.body)) : undefined,
      signal: init.signal ?? undefined,
    })
    const route = routes[`${method} ${path}`]
    const reply = typeof route === 'function' ? await route(init) : route
    if (init.signal?.aborted) throw new DOMException('aborted', 'AbortError')
    const r = reply ?? { status: 404, body: { error: `no route ${method} ${path}` } }
    return new Response(JSON.stringify(r.body ?? {}), {
      status: r.status ?? 200, headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fn)
  return { fn, calls }
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
