import { fireEvent, render, screen, within } from '@testing-library/svelte'
import { beforeEach, expect, test, vi } from 'vitest'
import { fakeFetch } from '../test/helpers'
import { createApi } from './api'
import { Live } from './live.svelte'
import type { Tab } from './router.svelte'
import WorkspaceDetail from './WorkspaceDetail.svelte'

const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })
const row = { key: 'k', name: 'mine', live: false, attached: false, files_rev: 1, flows_rev: 1 }
const FILES = { workspace: row, downloads: [], screenshots: [], files: [], browser: false }
const FLOWS = { enabled: true, flows: [], rev: 1, workspace: 'k' }
const SET = {
  text: 'flows:\n  global:\n  - login, nextcloud\n  - smoke\n',
  config: { 'flows.global': { value: ['login, nextcloud', 'smoke'], source: 'workspace' } },
  error: null,
  kept: true,
}
const UNSET = { text: null, config: { 'flows.global': { value: null, source: 'default' } }, error: null, kept: false }
const BROKEN = { ...UNSET, text: 'colour: blue\n', error: '`colour` is not a setting workspace.yaml holds: it holds flows.global', kept: true }

function setup(config: unknown, tab: Tab = 'config', routes = {}) {
  const net = fakeFetch({
    'GET /admin/workspaces/k/files': { body: FILES },
    'GET /admin/workspaces/k/flows': { body: FLOWS },
    'GET /admin/workspaces/k/config': { body: config },
    ...routes,
  })
  const live = new Live(api, '')
  const r = render(WorkspaceDetail, { key: 'k', tab, flow: undefined, api, live, root: '', liveScript: '' })
  return { ...r, ...net }
}
/* The element once it is there: waitFor retries only what throws. */
const found = (root: HTMLElement, selector: string) =>
  vi.waitFor(() => {
    const el = root.querySelector<HTMLElement>(selector)
    if (!el) throw new Error('no ' + selector)
    return el
  })

beforeEach(() => { history.replaceState(null, '', '/#/workspaces/k/config'); vi.stubGlobal('alert', vi.fn()) })

test('Config is the tab after History (UI-71)', () => {
  const { container } = setup(SET)
  const tabs = [...container.querySelectorAll('#workspaceTabs [role=tab]')].map((t) => t.id)
  expect(tabs.indexOf('tabConfig')).toBe(tabs.indexOf('tabHistory') + 1)
  expect(container.querySelector('#tabConfig')).toHaveAttribute('aria-selected', 'true')
})

test('a set filter reads as rows of tag chips with the workspace pill (UI-72)', async () => {
  const { container } = setup(SET)
  const setting = await found(container, '#paneConfig .setting[data-key="flows.global"]')
  const rows = [...setting.querySelectorAll('.filter-row')].map((r) => [...r.querySelectorAll('.tag')].map((t) => t.textContent))
  expect(rows).toEqual([['login', 'nextcloud'], ['smoke']])
  expect(setting).toHaveTextContent('or')
  expect(setting.querySelector('.pill.src.workspace')).toHaveTextContent('workspace')
  expect(container.querySelector('#paneConfig')).toHaveTextContent('never expires')
})

test('unset reads every global flow, from the default, and says it expires (UI-72, UI-74)', async () => {
  const { container } = setup(UNSET)
  const setting = await found(container, '#paneConfig .setting[data-key="flows.global"]')
  expect(setting).toHaveTextContent('every global flow')
  expect(setting.querySelector('.pill.src.default')).toHaveTextContent('default')
  expect(container.querySelector('#paneConfig')).toHaveTextContent('expires a day after its last use')
})

test('a save writes the YAML and shows what it now is (UI-71)', async () => {
  const { container, calls } = setup(UNSET, 'config', { 'PUT /admin/workspaces/k/config': { body: SET } })
  await fireEvent.click(await screen.findByRole('button', { name: 'Edit YAML' }))
  const editor = (await found(container, '.modal textarea.yaml') as HTMLTextAreaElement)
  await fireEvent.input(editor, { target: { value: SET.text } })
  await fireEvent.click(within(container.querySelector('.modal')!).getByRole('button', { name: 'Save' }))
  await vi.waitFor(() => expect(container.querySelector('.modal')).toBeNull())
  expect(calls.find((c) => c.method === 'PUT')!.body).toEqual({ text: SET.text })
  expect(container.querySelectorAll('#paneConfig .tag')).toHaveLength(3)
})

test('a refused save keeps the editor open with the reason under it (UI-71)', async () => {
  const { container } = setup(UNSET, 'config', {
    'PUT /admin/workspaces/k/config': { status: 400, body: { error: '`colour` is not a setting' } },
  })
  await fireEvent.click(await screen.findByRole('button', { name: 'Edit YAML' }))
  const editor = (await found(container, '.modal textarea.yaml') as HTMLTextAreaElement)
  await fireEvent.input(editor, { target: { value: 'colour: blue\n' } })
  await fireEvent.click(within(container.querySelector('.modal')!).getByRole('button', { name: 'Save' }))
  await vi.waitFor(() => expect(container.querySelector('.modal .refusal')).toHaveTextContent('`colour` is not a setting'))
  expect((await found(container, '.modal textarea.yaml') as HTMLTextAreaElement).value).toBe('colour: blue\n')
  expect(alert).not.toHaveBeenCalled()
})

test('a refused config shows its reason on every tab, with a way into the editor (UI-73)', async () => {
  for (const tab of ['session', 'files', 'flows', 'config'] as Tab[]) {
    const { container, unmount } = setup(BROKEN, tab)
    const banner = await found(container, '#configInvalid')
    expect(banner).toHaveTextContent("This workspace's config is invalid: `colour` is not a setting")
    await fireEvent.click(within(banner).getByRole('button', { name: 'Edit YAML' }))
    expect((await found(container, '.modal textarea.yaml') as HTMLTextAreaElement).value).toBe('colour: blue\n')
    unmount()
  }
})

test('a valid config shows no banner', async () => {
  const { container } = setup(SET, 'files')
  await vi.waitFor(() => expect(container.querySelector('#tabConfig')).toBeInTheDocument())
  await new Promise((r) => setTimeout(r, 0))
  expect(container.querySelector('#configInvalid')).toBeNull()
})
