import type { SessionRow } from './types'

const GLYPH: Record<string, string> = {
  pdf: '📄', png: '🖼️', jpg: '🖼️', jpeg: '🖼️', gif: '🖼️', webp: '🖼️', svg: '🖼️',
  html: '🌐', htm: '🌐', csv: '📊', json: '📊', xml: '📊',
  zip: '🗜️', gz: '🗜️', mp4: '🎬', webm: '🎬', mov: '🎬', txt: '📝', md: '📝',
}

/* Keyed on the capability the Grid reports — the browser actually running,
   not the one asked for. */
const BROWSER: Record<string, string> = { chrome: '🟢', firefox: '🦊', msedge: '🌊', edge: '🌊', safari: '🧭' }

const has = (map: Record<string, string>, key: string) => Object.hasOwn(map, key)

export function browserMark(name?: string | null): string {
  const key = String(name ?? '').toLowerCase()
  return has(BROWSER, key) ? BROWSER[key] : '🌐'
}

export function glyphFor(name: string): string {
  const ext = (name.split('.').pop() || '').toLowerCase()
  return has(GLYPH, ext) ? GLYPH[ext] : '📁'
}

/* Escaping makes a URL safe to display, not safe to click: `javascript:` is a
   URL too. */
export const safeHref = (u: unknown): string => (/^https?:\/\//i.test(String(u ?? '')) ? String(u) : '')

export function bytes(n: number): string {
  if (!Number.isFinite(n)) return ''
  if (n < 1024) return n + ' B'
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB'
  return (n / 1048576).toFixed(1) + ' MB'
}

export function ago(ms?: number | null, now = Date.now()): string {
  if (!ms) return ''
  const s = Math.max(0, (now - ms) / 1000)
  if (s < 60) return Math.round(s) + 's ago'
  if (s < 3600) return Math.round(s / 60) + 'm ago'
  return Math.round(s / 3600) + 'h ago'
}

/* Each folder gets its own word — any one can be zero without the others
   being. Older rows carry only files_count. */
export function countsText(s: SessionRow): string {
  const c = s.counts
  if (c) {
    return ([[c.downloads, ' download'], [c.screenshots, ' screenshot'], [c.files, ' file']] as const)
      .filter(([n]) => n)
      .map(([n, word]) => n + word + (n === 1 ? '' : 's'))
      .join(' · ')
  }
  return s.files_count === undefined || s.files_count === null
    ? '' : s.files_count + ' file' + (s.files_count === 1 ? '' : 's')
}

export function metaLine(s: SessionRow, now = Date.now()): string {
  const meta = countsText(s)
  return [s.browser, s.version].filter(Boolean).join(' ')
    + (meta ? ' · ' + meta : '')
    + (s.started ? ' · ' + ago(s.started * 1000, now) : '')
    + (s.node ? ' · ' + s.node : '')
}

/* The headline is the session, not the browser: a session outlives its browsers. */
export const sessionLabel = (s: SessionRow): string => s.name || s.owner || 'session'
