import { afterEach, expect, test } from 'vitest'
import { go, hashes, parse, replace, router, sync } from './router.svelte'

afterEach(() => { history.replaceState(null, '', '#/') })

test('parse (R2)', () => {
  expect(parse('')).toEqual({ view: 'list' })
  expect(parse('#/')).toEqual({ view: 'list' })
  expect(parse('#/console')).toEqual({ view: 'console' })
  expect(parse('#/secrets')).toEqual({ view: 'secrets' })
  expect(parse('#/sessions/a%20b')).toEqual({ view: 'session', key: 'a b', tab: 'files', flow: undefined })
  expect(parse('#/sessions/k/flows')).toEqual({ view: 'session', key: 'k', tab: 'flows', flow: undefined })
  expect(parse('#/sessions/k/flows/my%2Fflow')).toEqual({ view: 'session', key: 'k', tab: 'flows', flow: 'my/flow' })
  expect(parse('#/nonsense')).toEqual({ view: 'list' })
})

test('hashes round-trip through parse', () => {
  expect(parse(hashes.flow('a b', 'f/1'))).toEqual({ view: 'session', key: 'a b', tab: 'flows', flow: 'f/1' })
  expect(hashes.session('k')).toBe('#/sessions/k')
  expect(hashes.flows('k')).toBe('#/sessions/k/flows')
})

test('replace moves the address and the route without a history entry', () => {
  const before = history.length
  replace(hashes.flows('k'))
  expect(location.hash).toBe('#/sessions/k/flows')
  expect(router.route).toEqual({ view: 'session', key: 'k', tab: 'flows', flow: undefined })
  expect(history.length).toBe(before)
})

test('go routes through hashchange', async () => {
  window.addEventListener('hashchange', sync)
  go('#/secrets')
  await new Promise((r) => window.addEventListener('hashchange', r, { once: true }))
  expect(router.route).toEqual({ view: 'secrets' })
  window.removeEventListener('hashchange', sync)
})
