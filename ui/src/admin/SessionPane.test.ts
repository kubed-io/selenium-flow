import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, expect, test } from 'vitest'
import type { WorkspaceRow } from '../lib/types'
import { resetFolds } from './folds.svelte'
import SessionPane from './SessionPane.svelte'

afterEach(() => resetFolds())

const SENT = [
  { key: 'browserName', value: 'firefox', sources: ['option'] },
  { key: 'browserVersion', value: '142', sources: ['option'] },
  { key: 'kubed:pool', value: 'default', sources: ['config'] },
  { key: 'moz:firefoxOptions', value: { prefs: { a: 1, b: 2, c: 3, d: 4, e: 5, f: 6 } }, sources: ['flow', 'raw'] },
  { key: 'goog:chromeOptions', value: { args: ['--a'], prefs: { x: 1 }, binary: '/b' }, sources: ['raw'] },
  { key: 'unhandledPromptBehavior', value: 'ignore', sources: ['flow'] },
  { key: 'se:downloadsEnabled', value: true, sources: ['flow'] },
] as WorkspaceRow['sent']

const LIVE: WorkspaceRow = {
  key: 'k', name: 'mine', browser: 'firefox', asked_version: '142', live: true, attached: true,
  recording: true, session_id: '99e9bafa', window: '1280x900', started: 1_790_000_000, grid_timeout: 300,
  capture: true, capturing: true, captured: { console: 5, network: 24, errors: 0, failed_requests: 0 },
  ran: { version: '142.0', node: 'selenium-node-firefox-6c8d', live_view: true }, sent: SENT, ended: null,
}

const card = (c: HTMLElement, id: string) => c.querySelector('#' + id) as HTMLElement | null
const fact = (c: HTMLElement, id: string, k: string) =>
  [...(card(c, id)?.querySelectorAll('.fact') ?? [])].find((f) => f.querySelector('.k')?.textContent === k)?.querySelector('.v')?.textContent

test('a live workspace: what was asked, and what the Grid gave', () => {
  const { container } = render(SessionPane, { row: LIVE, hidden: false })
  expect(fact(container, 'workspaceCard', 'browser')).toBe('firefox · 142')
  expect(fact(container, 'workspaceCard', 'window')).toBe('1280×900')
  expect(fact(container, 'workspaceCard', 'record')).toBe('on')
  expect(fact(container, 'workspaceCard', 'capture')).toBe('on · receiving · 24 requests · 5 console entries')
  expect(fact(container, 'gridCard', 'browser')).toBe('firefox 142.0')
  expect(fact(container, 'gridCard', 'id')).toBe('99e9bafa')
  expect(fact(container, 'gridCard', 'node')).toBe('selenium-node-firefox-6c8d')
  expect(fact(container, 'gridCard', 'idle timeout')).toBe('300 s · read from the Grid node')
  expect(fact(container, 'gridCard', 'live view')).toBe('yes')
  expect(fact(container, 'gridCard', 'ended')).toBeUndefined()
})

test('no version asked reads as latest, and so does latest', () => {
  const none = render(SessionPane, { row: { ...LIVE, browser: 'chrome', asked_version: null }, hidden: false })
  expect(fact(none.container, 'workspaceCard', 'browser')).toBe('chrome · latest')
  none.unmount()
  const latest = render(SessionPane, { row: { ...LIVE, browser: undefined, asked_version: 'latest' }, hidden: false })
  expect(fact(latest.container, 'workspaceCard', 'browser')).toBe('chrome · latest')
})

test('LIVE VIEW says no when the Grid has none', () => {
  const { container } = render(SessionPane, { row: { ...LIVE, ran: { version: '1', node: 'n', live_view: false } }, hidden: false })
  expect(fact(container, 'gridCard', 'live view')).toBe('no')
})

test('an idle workspace: no RECORD or CAPTURE row, and ENDED by the admin in place of LIVE VIEW', () => {
  const at = 1_790_000_100
  const row = { ...LIVE, live: false, attached: false, session_id: null, ended: { at, by: 'admin' as const } }
  const { container } = render(SessionPane, { row, hidden: false })
  expect(fact(container, 'workspaceCard', 'record')).toBeUndefined()
  expect(fact(container, 'workspaceCard', 'capture')).toBeUndefined()
  expect(fact(container, 'workspaceCard', 'browser')).toBe('firefox · 142')
  expect(fact(container, 'gridCard', 'live view')).toBeUndefined()
  expect(fact(container, 'gridCard', 'ended')).toBe(new Date(at * 1000).toLocaleString() + ' · ended by the admin')
  expect(fact(container, 'gridCard', 'browser')).toBe('firefox 142.0')
})

test('ended by the caller', () => {
  const row = { ...LIVE, live: false, attached: false, ended: { at: 1, by: 'caller' as const } }
  const { container } = render(SessionPane, { row, hidden: false })
  expect(fact(container, 'gridCard', 'ended')).toMatch(/ · ended by the caller$/)
})

test('a browser the Grid no longer lists reads as reaped, or gone with no timeout known', () => {
  const reaped = render(SessionPane, { row: { ...LIVE, live: false, attached: true }, hidden: false })
  expect(fact(reaped.container, 'gridCard', 'ended')).toBe('reaped after 300 s idle')
  reaped.unmount()
  const gone = render(SessionPane, { row: { ...LIVE, live: false, attached: true, grid_timeout: null }, hidden: false })
  expect(fact(gone.container, 'gridCard', 'ended')).toBe('gone from the Grid')
})

test('the GRID SESSION card is not shown when no browser was ever opened', () => {
  const { container } = render(SessionPane, { row: { key: 'k', name: 'mine', live: false }, hidden: false })
  expect(card(container, 'gridCard')).toBeNull()
  expect(fact(container, 'workspaceCard', 'browser')).toBe('chrome · latest')
})

test('an unnamed workspace says its key and who holds it', () => {
  const { container } = render(SessionPane, { row: { key: 'stdio', owner: 'stdio' }, hidden: false })
  expect(fact(container, 'workspaceCard', 'key')).toBe('stdio')
  expect(fact(container, 'workspaceCard', 'held by')).toBe('stdio')
})

test('Capabilities starts collapsed, counts the rows, and opens on a click', async () => {
  const { container } = render(SessionPane, { row: LIVE, hidden: false })
  const section = container.querySelector('#capabilitiesSection')!
  expect(section.querySelector('#capabilitiesCount')).toHaveTextContent('7')
  expect(section.querySelector('button.title')).toHaveAttribute('aria-expanded', 'false')
  expect(section.querySelector('.cap')).toBeNull()
  await fireEvent.click(section.querySelector('button.title')!)
  expect(section.querySelectorAll('.cap')).toHaveLength(7)
})

test('a row is key, value and a pill per source; a vendor block is summarised', async () => {
  const { container } = render(SessionPane, { row: LIVE, hidden: false })
  await fireEvent.click(container.querySelector('#capabilitiesSection button.title')!)
  const rows = [...container.querySelectorAll('.cap')]
  const of = (key: string) => rows.find((r) => r.querySelector('.ck')?.textContent === key)!
  expect(of('kubed:pool').querySelector('.cv')).toHaveTextContent('default')
  expect([...of('kubed:pool').querySelectorAll('.pill.src')].map((p) => p.textContent)).toEqual(['config'])
  expect(of('moz:firefoxOptions').querySelector('.cv')).toHaveTextContent('prefs 6')
  const pills = [...of('moz:firefoxOptions').querySelectorAll('.pill.src')]
  expect(pills.map((p) => p.textContent)).toEqual(['flow', 'raw'])
  expect(pills[0]).toHaveClass('flow')
  expect(pills[1]).toHaveClass('raw')
  expect(of('goog:chromeOptions').querySelector('.cv')).toHaveTextContent('args 1 · prefs 1 · binary')
  expect(of('se:downloadsEnabled').querySelector('.cv')).toHaveTextContent('true')
})

test('a row with no sent, ran or ended draws without error', () => {
  // Review Focus 5: a workspace record from before this change.
  const { container } = render(SessionPane, { row: { key: 'k', name: 'old', live: false, attached: false, started: 5 }, hidden: false })
  expect(card(container, 'gridCard')).not.toBeNull()
  expect(container.querySelector('#capabilitiesCount')).toHaveTextContent('0')
  expect(screen.queryByText('undefined')).toBeNull()
})

test('hidden hides the pane', () => {
  const { container } = render(SessionPane, { row: LIVE, hidden: true })
  expect(container.querySelector('#paneSession')).toHaveAttribute('hidden')
})

const pillsOf = (c: HTMLElement, k: string) =>
  [...(([...(card(c, 'workspaceCard')?.querySelectorAll('.fact') ?? [])].find((f) => f.querySelector('.k')?.textContent === k))?.querySelectorAll('.pills .pill.src') ?? [])]

test('each WORKSPACE row carries its sources as pills', () => {
  const row = { ...LIVE, set_by: { browser: 'config', version: 'caller', width: 'client', height: 'client' } } as WorkspaceRow
  const { container } = render(SessionPane, { row, hidden: false })
  const b = pillsOf(container, 'browser')
  expect(b.map((p) => p.textContent)).toEqual(['config', 'caller'])
  expect(b[0]).toHaveClass('config')
  expect(pillsOf(container, 'window').map((p) => p.textContent)).toEqual(['client'])
  expect(fact(container, 'workspaceCard', 'browser')).toBe('firefox · 142')
})

test('a setting nobody set has no pill', () => {
  const { container } = render(SessionPane, { row: { ...LIVE, set_by: {} }, hidden: false })
  expect(card(container, 'workspaceCard')!.querySelector('.pill.src')).toBeNull()
})

test('INSECURE shows only when on, idle too', () => {
  const on = render(SessionPane, { row: { ...LIVE, live: false, attached: false, insecure: true, set_by: { insecure: 'caller' } }, hidden: false })
  expect(fact(on.container, 'workspaceCard', 'insecure')).toBe('on')
  expect(pillsOf(on.container, 'insecure').map((p) => p.textContent)).toEqual(['caller'])
  on.unmount()
  const off = render(SessionPane, { row: { ...LIVE, insecure: false }, hidden: false })
  expect(fact(off.container, 'workspaceCard', 'insecure')).toBeUndefined()
})

test('CAPTURE reads off', () => {
  const { container } = render(SessionPane, { row: { ...LIVE, capture: false, capturing: false }, hidden: false })
  expect(fact(container, 'workspaceCard', 'capture')).toBe('off')
})

test('WINDOW reads with ×', () => {
  const { container } = render(SessionPane, { row: LIVE, hidden: false })
  expect(fact(container, 'workspaceCard', 'window')).toBe('1280×900')
})

test('a row the Grid did not list is neither reaped nor idle', () => {
  const { container } = render(SessionPane, { row: { ...LIVE, live: false, grid_listed: false, capturing: false }, hidden: false })
  expect(fact(container, 'gridCard', 'ended')).toBeUndefined()
  expect(fact(container, 'gridCard', 'live view')).toBeUndefined()
  expect(fact(container, 'gridCard', 'grid')).toBe('not answering')
  expect(fact(container, 'gridCard', 'idle timeout')).toBe('300 s · read from the Grid node')
  expect(fact(container, 'workspaceCard', 'capture')).toBe('on')
  expect(fact(container, 'workspaceCard', 'record')).toBe('on')
})

test('the Capabilities fold is kept per workspace', async () => {
  const open = (c: HTMLElement) => c.querySelector('#capabilitiesSection button.title')!.getAttribute('aria-expanded')
  const a = render(SessionPane, { row: { ...LIVE, key: 'a' }, hidden: false })
  await fireEvent.click(a.container.querySelector('#capabilitiesSection button.title')!)
  expect(open(a.container)).toBe('true')
  a.unmount()
  const b = render(SessionPane, { row: { ...LIVE, key: 'b' }, hidden: false })
  expect(open(b.container)).toBe('false')
  b.unmount()
  const again = render(SessionPane, { row: { ...LIVE, key: 'a' }, hidden: false })
  expect(open(again.container)).toBe('true')
})
