import { afterEach, expect, test } from 'vitest'
import { go, hashes, parse, replace, router, sync } from './router.svelte'

afterEach(() => { history.replaceState(null, '', '#/') })

test('parse (R2)', () => {
  expect(parse('')).toEqual({ view: 'list' })
  expect(parse('#/')).toEqual({ view: 'list' })
  expect(parse('#/console')).toEqual({ view: 'console' })
  expect(parse('#/secrets')).toEqual({ view: 'secrets' })
  expect(parse('#/settings')).toEqual({ view: 'settings' })
  expect(parse('#/workspaces/a%20b')).toEqual({ view: 'workspace', key: 'a b', tab: 'files', flow: undefined })
  expect(parse('#/workspaces/k/flows')).toEqual({ view: 'workspace', key: 'k', tab: 'flows', flow: undefined })
  expect(parse('#/workspaces/k/flows/my%2Fflow')).toEqual({ view: 'workspace', key: 'k', tab: 'flows', flow: 'my/flow' })
  expect(parse('#/workspaces/x/site-data')).toEqual({ view: 'workspace', key: 'x', tab: 'site-data', flow: undefined })
  expect(parse('#/nonsense')).toEqual({ view: 'list' })
})

test('a malformed escape in a pasted hash falls back to the list, not a dead app', () => {
  expect(parse('#/workspaces/%E0%A4%A')).toEqual({ view: 'list' })
  expect(parse('#/workspaces/k/flows/%E0%A4%A')).toEqual({ view: 'list' })
})

test('hashes round-trip through parse', () => {
  expect(parse(hashes.flow('a b', 'f/1'))).toEqual({ view: 'workspace', key: 'a b', tab: 'flows', flow: 'f/1' })
  expect(hashes.workspace('k')).toBe('#/workspaces/k')
  expect(hashes.flows('k')).toBe('#/workspaces/k/flows')
  expect(hashes.siteData('a b')).toBe('#/workspaces/a%20b/site-data')
  expect(parse(hashes.siteData('a b'))).toEqual({ view: 'workspace', key: 'a b', tab: 'site-data', flow: undefined })
  expect(hashes.settings).toBe('#/settings')
})

test('replace moves the address and the route without a history entry', () => {
  const before = history.length
  replace(hashes.flows('k'))
  expect(location.hash).toBe('#/workspaces/k/flows')
  expect(router.route).toEqual({ view: 'workspace', key: 'k', tab: 'flows', flow: undefined })
  expect(history.length).toBe(before)
})

test('go routes through hashchange', async () => {
  window.addEventListener('hashchange', sync)
  go('#/secrets')
  await new Promise((r) => window.addEventListener('hashchange', r, { once: true }))
  expect(router.route).toEqual({ view: 'secrets' })
  window.removeEventListener('hashchange', sync)
})

test('History is a tab, and a Site data host rides in the hash', () => {
  expect(parse('#/workspaces/k/history')).toEqual({ view: 'workspace', key: 'k', tab: 'history', flow: undefined, site: undefined })
  expect(parse('#/workspaces/k/site-data/app.example.com')).toEqual({ view: 'workspace', key: 'k', tab: 'site-data', flow: undefined, site: 'app.example.com' })
  expect(parse('#/workspaces/k/files/x')).toEqual({ view: 'workspace', key: 'k', tab: 'files', flow: undefined, site: undefined })
  expect(hashes.history('a b')).toBe('#/workspaces/a%20b/history')
  expect(parse(hashes.site('a b', 'h.example.com'))).toEqual({ view: 'workspace', key: 'a b', tab: 'site-data', flow: undefined, site: 'h.example.com' })
})
