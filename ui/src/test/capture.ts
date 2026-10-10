import { vi } from 'vitest'
import type { ConsoleData, NetworkData, RequestRow } from '../lib/types'

/* An EventSource that records its URL and lets a test speak for the server. */
export class FakeEventSource {
  static last: FakeEventSource
  url: string
  closed = false
  onmessage: ((e: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  constructor(url: string) { this.url = url; FakeEventSource.last = this }
  emit(row: unknown) { this.onmessage?.({ data: JSON.stringify(row) }) }
  close() { this.closed = true }
}
export const stubEventSource = () => vi.stubGlobal('EventSource', FakeEventSource)

export const req = (o: Partial<RequestRow>): RequestRow => ({
  id: '1.1', seq: 1, at: 1760100000, method: 'GET', url: 'https://a.test/x', status: 200, type: 'fetch',
  size: 1200, took_ms: 40, failed: false, error: null, context: '', nav: 1, navigation: false, ...o,
})

export const networkData: NetworkData & { tail_url: string } = {
  capture: true, capturing: true, pages: [{ nav: 1, url: 'https://a.test/', at: 1760100000 }],
  requests: [
    req({ id: '1.1', seq: 1, type: 'document' }),
    req({ id: '1.2', seq: 2, type: 'fetch', status: 500 }),
    req({ id: '1.3', seq: 3, type: 'xhr' }),
    req({ id: '1.4', seq: 4, type: 'image' }),
    req({ id: '1.5', seq: 5, type: 'fetch', status: null, failed: true, error: 'net::ERR_FAILED', size: null }),
  ],
  count: 5, total: 5, not_kept: { static: 19 }, tail_url: '/admin/workspaces/ws/tail?exp=1&sig=x',
}

export const consoleData: ConsoleData & { tail_url: string } = {
  capture: true, capturing: true, pages: [{ nav: 1, url: 'https://a.test/', at: 1760100000 }],
  entries: [
    { seq: 1, at: 1760100000, level: 'log', type: 'console', text: 'hello', source: { url: 'https://a.test/js/app.js', line: 3 }, context: '', nav: 1 },
    { seq: 2, at: 1760100001, level: 'error', type: 'console', text: 'boom <b>x</b>', source: null, context: '', nav: 1 },
    { seq: 3, at: 1760100002, level: 'warn', type: 'console', text: 'careful', source: null, context: '', nav: 1 },
  ],
  count: 3, total: 3, not_kept: { debug: 2 }, tail_url: '/admin/workspaces/ws/tail?exp=1&sig=x',
}
