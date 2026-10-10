import type { ConsoleEntry, RequestRow } from './types'

/* Shared by the app's views and the admin's panes. Everything here returns text
   for a text node: captured data is never markup. */

/* Bytes as kilobytes of 1000: 412 -> "0.4 kB". */
export const kb = (bytes: number | null): string => (bytes === null || !Number.isFinite(bytes) ? '—' : `${(bytes / 1000).toFixed(1)} kB`)

/* Epoch seconds as a local 24-hour clock time: "12:01:43". */
export const clock = (at: number): string => new Date(at * 1000).toLocaleTimeString('en-GB', { hour12: false })

export const statusText = (r: RequestRow): string => (r.failed ? 'failed' : String(r.status ?? ''))

export const isError = (e: ConsoleEntry): boolean => e.level === 'error' || e.type === 'javascript'

export const isFailed = (r: RequestRow): boolean => r.failed || (r.status ?? 0) >= 400

/* A URL as host and path, with no scheme; what does not parse is shown as it is. */
export function hostPath(url: string): string {
  try {
    const u = new URL(url)
    return u.host + u.pathname + u.search
  } catch {
    return url.replace(/^[a-z][a-z0-9+.-]*:\/\//i, '')
  }
}

/* A console source as a file name: the last segment of its path, the host for
   a root path, and never its query or fragment. */
export function sourceName(url: string): string {
  try {
    const u = new URL(url)
    return u.pathname.split('/').pop() || u.host || url.split(/[?#]/)[0]
  } catch {
    return url.split(/[?#]/)[0].split('/').pop() ?? ''
  }
}

/* Only the path and query, for a title. */
export function pathOf(url: string): string {
  try {
    const u = new URL(url)
    return u.pathname + u.search
  } catch {
    return url
  }
}

export const CAPTURE_OFF = 'Capture is off for this workspace. Open the browser with open_session(capture=true).'
