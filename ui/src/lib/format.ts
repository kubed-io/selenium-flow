import type { WorkspaceRow } from './types'

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

/* How long the Grid lets a session sit idle, in seconds, and where that came
   from, as the boards `summary / live` and `summary / idle` draw it; nothing
   when the Grid did not say. */
export function idle(seconds?: number | null, live?: boolean): string {
  if (!seconds || seconds <= 0) return ''
  return `${seconds} s · ${live ? 'read from the Grid node' : 'no session is open'}`
}

/* Each folder gets its own word — any one can be zero without the others
   being. Older rows carry only files_count. */
export function countsText(s: WorkspaceRow): string {
  const c = s.counts
  if (c) {
    return ([[c.downloads, ' download'], [c.screenshots, ' screenshot'], [c.recordings ?? 0, ' recording'], [c.files, ' file']] as const)
      .filter(([n]) => n)
      .map(([n, word]) => n + word + (n === 1 ? '' : 's'))
      .join(' · ')
  }
  return s.files_count === undefined || s.files_count === null
    ? '' : s.files_count + ' file' + (s.files_count === 1 ? '' : 's')
}

/* Who opened a workspace's browser: the OIDC username, or the token. */
export const openerText = (o: NonNullable<WorkspaceRow['opened_by']>): string =>
  o.kind === 'admin' ? 'token' : o.username || 'OIDC'

export function metaLine(s: WorkspaceRow, now = Date.now()): string {
  const meta = countsText(s)
  return [s.browser, s.version].filter(Boolean).join(' ')
    + (meta ? ' · ' + meta : '')
    + (s.started ? ' · ' + ago(s.started * 1000, now) : '')
    + (s.node ? ' · ' + s.node : '')
    + (s.opened_by ? ' · by ' + openerText(s.opened_by) : '')
}

/* The headline is the workspace, not the browser: a workspace outlives its browsers. */
export const workspaceLabel = (s: WorkspaceRow): string => s.name || s.owner || 'workspace'

/* One line for a step: the first string argument (a selector, url or text),
   a selector object read as its xpath or css. */
export function stepSummary(args: unknown, max = 60): string {
  if (!args || typeof args !== 'object') return ''
  for (const v of Object.values(args as Record<string, unknown>)) {
    let s: unknown = v
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      const o = v as Record<string, unknown>
      s = o.css ?? o.xpath
    }
    if (typeof s === 'string' && s) return s.length > max ? s.slice(0, max - 1) + '…' : s
  }
  return ''
}

export const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`

export const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)

/* A flow's `parameters`, read as the server's `Shape` reads it: a flow is YAML a
   person may have written by hand, so nothing about its shape is guaranteed. */
export function flowParams(p: unknown): { properties: Record<string, unknown>; required: string[] } {
  const r = isRecord(p) ? p.required : undefined
  return {
    properties: isRecord(p) && isRecord(p.properties) ? p.properties : {},
    required: Array.isArray(r) ? r.filter((s): s is string => typeof s === 'string') : [],
  }
}
