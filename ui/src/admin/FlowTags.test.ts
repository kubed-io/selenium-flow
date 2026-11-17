import { fireEvent, render } from '@testing-library/svelte'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { fakeFetch } from '../test/helpers'
import { createApi } from './api'
import { Live } from './live.svelte'
import { inUse, passes } from './tags'
import WorkspaceDetail from './WorkspaceDetail.svelte'

const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })
const DOC = { name: 'login', shared: false, steps: [], uses: {}, yaml: '' }
const LIST = {
  enabled: true, workspace: 'k', rev: 1,
  flows: [
    { name: 'login', step_count: 4, tags: ['login', 'demo'] },
    { name: 'nc-login', step_count: 5, shared: true, tags: ['login', 'nextcloud', 'auth:ldap'] },
    { name: 'nc-smoke', step_count: 4, tags: ['smoke', 'nextcloud'] },
    { name: 'plain', step_count: 1 },
  ],
}

function setup(routes = {}, flow: string | undefined = 'login') {
  const net = fakeFetch({
    'GET /admin/workspaces/k/files': { body: { workspace: { key: 'k', flows_rev: 1 }, downloads: [], screenshots: [], files: [] } },
    'GET /admin/workspaces/k/flows': { body: LIST },
    'GET /admin/workspaces/k/flows/login': { body: DOC },
    ...routes,
  })
  const live = new Live(api, '')
  const r = render(WorkspaceDetail, { key: 'k', tab: 'flows', flow, api, live, root: '', liveScript: '' })
  return { ...r, ...net }
}
beforeEach(() => {
  history.replaceState(null, '', '/#/workspaces/k/flows/login')
  vi.stubGlobal('alert', vi.fn())
  Element.prototype.scrollIntoView = vi.fn() as unknown as Element['scrollIntoView']
})
afterEach(() => { delete (Element.prototype as Partial<Element>).scrollIntoView })

function found<E extends Element = HTMLElement>(root: ParentNode, selector: string): E {
  const el = root.querySelector<E>(selector)
  if (!el) throw new Error('not yet: ' + selector)
  return el
}
const texts = (root: ParentNode, selector: string) => [...root.querySelectorAll(selector)].map((e) => e.textContent?.trim())
const names = (c: HTMLElement) => texts(c, '.flowlist .item .nm')

test('a filter row passes a flow carrying all its tags, and any row will do (UI-70)', () => {
  expect(passes([['login', 'nextcloud'], ['smoke']], ['nextcloud', 'login'])).toBe(true)
  expect(passes([['login', 'nextcloud'], ['smoke']], ['smoke'])).toBe(true)
  expect(passes([['login', 'nextcloud'], ['smoke']], ['login'])).toBe(false)
  expect(passes([['login']], undefined)).toBe(false)
  // No tag picked yet filters nothing out.
  expect(passes([[]], ['x'])).toBe(true)
  expect(passes([], undefined)).toBe(true)
})

test('the tags in use are counted over the listing, in tag order (UI-76)', () => {
  expect(inUse(LIST.flows)).toEqual([['auth:ldap', 1], ['demo', 1], ['login', 2], ['nextcloud', 2], ['smoke', 1]])
  expect(inUse([{ tags: 'x' }, {}])).toEqual([])
})

test('each flow in the list shows its tags under it, and its detail shows them as chips (UI-76)', async () => {
  const { container } = setup()
  await vi.waitFor(() => expect(container.querySelectorAll('.flowlist .item')).toHaveLength(4))
  const [login, , , plain] = container.querySelectorAll('.flowlist .item')
  expect(texts(login, '.tags .tag')).toEqual(['login', 'demo'])
  expect(plain.querySelector('.tags')).toBeNull()
  const panel = await vi.waitFor(() => found(container, '#flowpanel .panel'))
  expect(texts(panel, '.tags .tag .t')).toEqual(['login', 'demo'])
})

test('picking tags builds a row, or adds another, and the list says how many pass (UI-70)', async () => {
  const { container } = setup()
  await vi.waitFor(() => expect(names(container)).toHaveLength(4))
  const bar = found(container, '.tagfilter')
  expect(found(bar, '.count')).toHaveTextContent('4 of 4')

  await fireEvent.click(found(bar, '.addtag'))
  const offered = texts(bar, '.offer li')
  expect(offered).toEqual(['auth:ldap1', 'demo1', 'login2', 'nextcloud2', 'smoke1'])
  await fireEvent.input(found(bar, 'input'), { target: { value: 'NEXT' } })
  expect(texts(bar, '.offer li')).toEqual(['nextcloud2'])
  await fireEvent.click(found(bar, '.offer li'))
  expect(names(container)).toEqual(['nc-login', 'nc-smoke'])

  await fireEvent.click(found(bar, '.addtag'))
  // A tag the row already holds is not offered again.
  expect(texts(bar, '.offer li')).not.toContain('nextcloud2')
  await fireEvent.input(found(bar, 'input'), { target: { value: 'login' } })
  await fireEvent.keyDown(found(bar, 'input'), { key: 'Enter' })
  expect(names(container)).toEqual(['nc-login'])
  expect(found(bar, '.count')).toHaveTextContent('1 of 4')

  await fireEvent.click(found(bar, '.addrow'))
  expect(bar.querySelectorAll('.frow')).toHaveLength(2)
  const second = bar.querySelectorAll('.frow')[1]
  await fireEvent.click(found(second, '.addtag'))
  await fireEvent.input(found(second, 'input'), { target: { value: 'smoke' } })
  await fireEvent.keyDown(found(second, 'input'), { key: 'Enter' })
  expect(names(container)).toEqual(['nc-login', 'nc-smoke'])
  expect(found(bar, '.count')).toHaveTextContent('2 of 4')

  // Taking a tag out of a row widens it again.
  await fireEvent.click(found(bar.querySelectorAll('.frow')[0], '.tag button'))
  expect(names(container)).toEqual(['login', 'nc-login', 'nc-smoke'])
  expect(texts(bar.querySelectorAll('.frow')[0], '.tag .t')).toEqual(['login'])
})

test('no flow with a tag, no filter bar (UI-70)', async () => {
  const { container } = setup({ 'GET /admin/workspaces/k/flows': { body: { ...LIST, flows: [{ name: 'login', step_count: 1 }] } } })
  await vi.waitFor(() => expect(names(container)).toEqual(['login']))
  expect(container.querySelector('.tagfilter')).toBeNull()
})

test('a filter whose bar went with the last tag filters nothing (UI-70)', async () => {
  let listings = 0
  const untagged = { ...LIST, flows: LIST.flows.map(({ name, step_count, shared }) => ({ name, step_count, shared })) }
  const { container } = setup({
    'GET /admin/workspaces/k/flows': () => ({ body: listings++ ? untagged : LIST }),
    'PUT /admin/workspaces/k/flows/login/tags': { body: {} },
  })
  const bar = await vi.waitFor(() => found(container, '.tagfilter'))
  await fireEvent.click(found(bar, '.addtag'))
  await fireEvent.input(found(bar, 'input'), { target: { value: 'demo' } })
  await fireEvent.keyDown(found(bar, 'input'), { key: 'Enter' })
  expect(names(container)).toEqual(['login'])
  const panel = await vi.waitFor(() => found(container, '#flowpanel .panel'))
  await fireEvent.click(found(panel, '.tags .tag button'))
  await vi.waitFor(() => expect(container.querySelector('.tagfilter')).toBeNull())
  expect(names(container)).toEqual(['login', 'nc-login', 'nc-smoke', 'plain'])
})

test('adding a tag offers the ones in use and saves at once (UI-76)', async () => {
  const { container, calls } = setup({ 'PUT /admin/workspaces/k/flows/login/tags': { body: {} } })
  const panel = await vi.waitFor(() => found(container, '#flowpanel .panel'))
  await fireEvent.click(found(panel, '.addtag'))
  // The flow's own tags are not offered.
  expect(texts(panel, '.offer li')).toEqual(['auth:ldap1', 'nextcloud2', 'smoke1'])
  await fireEvent.input(found(panel, 'input'), { target: { value: ' Fresh ' } })
  await fireEvent.keyDown(found(panel, 'input'), { key: 'Enter' })
  await vi.waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ tags: ['login', 'demo', 'fresh'] }))
  await vi.waitFor(() => expect(calls.filter((c) => c.path === '/admin/workspaces/k/flows')).toHaveLength(2))
})

test('removing a tag saves at once, and a refusal is said (UI-76)', async () => {
  const { container, calls } = setup({ 'PUT /admin/workspaces/k/flows/login/tags': { status: 400, body: { error: 'nope' } } })
  const panel = await vi.waitFor(() => found(container, '#flowpanel .panel'))
  await fireEvent.click(found(panel, '.tags .tag button'))
  await vi.waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ tags: ['demo'] }))
  await vi.waitFor(() => expect(alert).toHaveBeenCalledWith('Could not save the tags: nope'))
})

test('the arrows move through the offers and Enter takes the one marked (UI-76)', async () => {
  const { container } = setup()
  const bar = await vi.waitFor(() => found(container, '.tagfilter'))
  await fireEvent.click(found(bar, '.addtag'))
  const input = found(bar, 'input')
  await fireEvent.keyDown(input, { key: 'ArrowDown' })
  await fireEvent.keyDown(input, { key: 'ArrowDown' })
  expect(found(bar, '.offer li[aria-selected=true]')).toHaveTextContent('demo1')
  expect(input.getAttribute('aria-activedescendant')).toBe(found(bar, '.offer li[aria-selected=true]').id)
  await fireEvent.keyDown(input, { key: 'Enter' })
  expect(names(container)).toEqual(['login'])
  await fireEvent.click(found(bar, '.addtag'))
  await fireEvent.keyDown(found(bar, 'input'), { key: 'Escape' })
  expect(bar.querySelector('input')).toBeNull()
})
