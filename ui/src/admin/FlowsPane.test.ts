import { fireEvent, render, within } from '@testing-library/svelte'
import { beforeEach, expect, test, vi } from 'vitest'
import { deferred, FakeEventSource, fakeFetch } from '../test/helpers'
import Admin from './Admin.svelte'
import { createApi } from './api'
import { Live } from './live.svelte'
import SessionDetail from './SessionDetail.svelte'

const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })
const DOC = {
  name: 'login', shared: false, description: 'Signs in.',
  parameters: { properties: { user: { type: 'string', description: 'who' }, n: { type: 'integer', default: 2 } }, required: ['user'] },
  steps: [
    { tool: 'navigate', args: { url: '${site}/' } },
    { id: 'type-pass', tool: 'write', args: { selector: { css: '#p' }, secret: { name: 'admin', key: 'token' } }, onError: 'continue' },
    { tool: 'execute_script', args: { script: 'a;\nb;' } },
    null,
  ],
  uses: { user: [1] },
  yaml: '# comments survive\nsteps: []\n',
}
const LIST = { enabled: true, session: 'k', rev: 1, flows: [{ name: 'login', step_count: 4 }, { name: 'shared-one', step_count: 1, shared: true }] }

function setup(routes = {}, flow: string | undefined = 'login') {
  const net = fakeFetch({
    'GET /admin/sessions/k/files': { body: { session: { key: 'k', flows_rev: 1 }, downloads: [], screenshots: [], files: [] } },
    'GET /admin/sessions/k/flows': { body: LIST },
    'GET /admin/sessions/k/flows/login': { body: DOC },
    ...routes,
  })
  const live = new Live(api, '')
  const r = render(SessionDetail, { key: 'k', tab: 'flows', flow, api, live, root: '' })
  return { ...r, ...net, live }
}
beforeEach(() => { history.replaceState(null, '', '/#/sessions/k/flows/login'); vi.stubGlobal('alert', vi.fn()) })

const tick = () => new Promise((r) => setTimeout(r, 0))
/* `vi.waitFor` retries only on a throw; a null return would pass at once. */
function found<E extends Element = HTMLElement>(root: ParentNode, selector: string): E {
  const el = root.querySelector<E>(selector)
  if (!el) throw new Error('not yet: ' + selector)
  return el
}

test('the list: names, step counts, a globe only when shared, the open one selected (W1)', async () => {
  const { container } = setup()
  await vi.waitFor(() => expect(container.querySelectorAll('.flowlist .item')).toHaveLength(2))
  const [a, b] = container.querySelectorAll('.flowlist .item')
  expect(a).toHaveTextContent('login4 steps')
  expect(a.querySelector('.globe')).toBeNull()
  expect(b).toHaveTextContent('shared-one1 step')
  expect(b.querySelector('.globe')).toHaveAttribute('aria-label', 'global')
  expect(b.querySelector('.globe')).toHaveAttribute('title', 'In the shared global library')
  await vi.waitFor(() => expect(a).toHaveAttribute('aria-selected', 'true'))
  expect(b).toHaveAttribute('aria-selected', 'false')
})

test('flows off, and none yet (W1)', async () => {
  const off = setup({ 'GET /admin/sessions/k/flows': { body: { enabled: false } } }, undefined)
  await vi.waitFor(() => expect(off.container).toHaveTextContent('Flows are off: this server was started with no FLOW_DATA_DIR.'))
  expect(off.container.querySelector('#flowsTotal')).toHaveTextContent('off')
  off.unmount()
  const none = setup({ 'GET /admin/sessions/k/flows': { body: { enabled: true, flows: [] } } }, undefined)
  await vi.waitFor(() => expect(none.container).toHaveTextContent('No flows yet.'))
})

test('a listing that fails shows its error in place of the tab (W1)', async () => {
  const { container } = setup({ 'GET /admin/sessions/k/flows': { status: 500, body: { error: 'boom' } } }, undefined)
  await vi.waitFor(() => expect(within(container.querySelector('#flows')!).getByText('boom')).toHaveClass('empty', 'error'))
})

test('the Flows tab has no accordion to open or close (W9)', async () => {
  const { container } = setup()
  await vi.waitFor(() => found(container, '.outline'))
  const pane = container.querySelector('#paneFlows')!
  // Today's wrapper, kept: a plain section box around #flows, not a <section> with a toggle.
  expect(pane.querySelector(':scope > .section > .body > #flows')).not.toBeNull()
  expect(pane.querySelectorAll('section, [data-toggle], [aria-expanded], [aria-controls], .caret')).toHaveLength(0)
})

test('the outline: params first with type glyph and required star; steps with tool glyph, id and lock (W3)', async () => {
  const { container } = setup()
  await vi.waitFor(() => found(container, '.outline'))
  const [params, steps] = container.querySelectorAll('.outline .rows')
  expect(params.querySelector('[data-param="user"] .req')).toHaveAttribute('aria-label', 'required')
  expect(params.querySelector('[data-param="user"] .icon')).toHaveTextContent('"')
  expect(params.querySelector('[data-param="n"] .req')).toBeNull()
  expect(params.querySelector('[data-param="n"] .icon')).toHaveAttribute('aria-label', 'integer')
  const rows = steps.querySelectorAll('.row')
  expect(rows[0].querySelector('.icon')).toHaveAttribute('title', 'navigate')
  expect(rows[0].querySelector('.icon')).toHaveAttribute('role', 'img')
  expect(rows[0].querySelector('.icon')).toHaveTextContent('🧭')
  expect(rows[0].querySelector('.chip')).toHaveTextContent('navigate')
  expect(rows[1].querySelector('.chip')).toHaveTextContent('type-pass')
  expect(rows[1].querySelector('.lock')).toHaveAttribute('aria-label', 'types a secret')
  expect(rows[0].querySelector('.lock')).toBeNull()
  expect(steps.querySelector('.req')).toBeNull() // required is a parameter's mark only
  expect(rows[3].querySelector('.icon')).toHaveTextContent('❓') // a [null] step never breaks the panel (W8)
  expect(container).toHaveTextContent('Pick a parameter or a step to see what it holds.')
  // The actions, in the panel head, each still carrying its word.
  const head = container.querySelector('.panel > .head')!
  expect(head.querySelector('.nm')).toHaveTextContent('login')
  expect(head.querySelector('.pill')).toBeNull()
  expect(head.querySelector('[data-edit]')).toHaveAttribute('aria-label', 'Edit YAML')
  expect(head.querySelector('[data-edit]')).toHaveTextContent('✏️')
  expect(head.querySelector('[data-move]')).toHaveTextContent('🌐')
  expect(head.querySelector('[data-drop]')).toHaveAttribute('aria-label', 'Delete this flow')
  expect(head.querySelector('[data-drop]')).toHaveClass('danger')
  expect(head.querySelector('[data-drop]')).toHaveTextContent('🗑️')
  expect(container.querySelector('.panel > .desc')).toHaveTextContent('Signs in.')
  expect([...container.querySelectorAll('.outline .olabel')].map((l) => l.textContent)).toEqual(['Params', 'Steps'])
})

test('the outline never numbers its rows; only a parameter’s used-by citations do (W3)', async () => {
  const { container } = setup()
  await vi.waitFor(() => found(container, '.outline'))
  expect(container.querySelectorAll('.outline .n')).toHaveLength(0)
  await fireEvent.click(container.querySelector('.outline [data-param="user"]')!)
  const cite = container.querySelector('.pane .rows [data-step="1"]')!
  expect(cite.querySelector('.n')).toHaveTextContent('2')
  expect(container.querySelectorAll('.outline .n')).toHaveLength(0)
})

test('step detail: number, chip, tool, arguments, code, selector, secret, behaviour (W3)', async () => {
  const { container } = setup()
  await vi.waitFor(() => found(container, '[data-step="1"]'))
  await fireEvent.click(container.querySelector('.outline [data-step="1"]')!)
  expect(container.querySelector('.outline [data-step="1"]')).toHaveAttribute('aria-selected', 'true')
  const pane = container.querySelector('.pane') as HTMLElement
  expect(pane.querySelector('.dhead')).toHaveTextContent('2type-passwrite')
  expect(within(pane).getByText('css #p')).toBeInTheDocument()
  expect(within(pane).getByText('admin / token')).toBeInTheDocument()
  expect(within(pane).getByText('onError').nextElementSibling).toHaveTextContent('continue')
  await fireEvent.click(container.querySelector('.outline [data-step="0"]')!)
  // A reference is shown as written; the chip is the tool, so it is not said twice.
  expect(within(container.querySelector('.pane') as HTMLElement).getByText('${site}/')).toHaveClass('pv')
  expect(container.querySelector('.pane .dhead .ty')).toBeNull()
  expect(container.querySelector('.pane')).not.toHaveTextContent('Behaviour')
  await fireEvent.click(container.querySelector('.outline [data-step="2"]')!)
  expect(container.querySelector('.pane pre.pv.code')!.textContent).toBe('a;\nb;')
  expect(container.querySelector('.pane .pair.block .pk')).toHaveTextContent('script')
  await fireEvent.click(container.querySelector('.outline [data-step="2"]')!)
  expect(container).toHaveTextContent('Pick a parameter or a step to see what it holds.') // unpick
})

test('param detail: required, description, default, used-by rows that jump (W3)', async () => {
  const { container } = setup()
  await vi.waitFor(() => found(container, '[data-param="user"]'))
  await fireEvent.click(container.querySelector('.outline [data-param="user"]')!)
  const pane = container.querySelector('.pane') as HTMLElement
  expect(within(pane).getByText('required')).toHaveClass('pill')
  expect(pane).toHaveTextContent('who')
  await fireEvent.click(pane.querySelector('.rows [data-step="1"]')!)
  expect(container.querySelector('.pane .dhead')).toHaveTextContent('type-pass')
  await fireEvent.click(container.querySelector('.outline [data-param="n"]')!)
  expect(container.querySelector('.pane')).toHaveTextContent('No step reads ${n}. Supplying it would change nothing.')
  expect(container.querySelector('.pane code')).toHaveTextContent('${n}')
  expect(container.querySelector('.pane')).toHaveTextContent('optional')
  expect(container.querySelector('.pane .pairs')).toHaveTextContent('default2')
})

test('a step index repeated in `uses` is cited twice, not thrown on (W8)', async () => {
  const { container } = setup({ 'GET /admin/sessions/k/flows/login': { body: { ...DOC, uses: { user: [1, 1] } } } })
  await vi.waitFor(() => found(container, '[data-param="user"]'))
  await fireEvent.click(container.querySelector('.outline [data-param="user"]')!)
  expect(container.querySelectorAll('.pane .rows [data-step="1"]')).toHaveLength(2)
})

test('a parameter declared empty and a step that is an empty object are shown, not gone (W8)', async () => {
  const doc = { name: 'login', parameters: { properties: { term: null } }, steps: [{}], uses: { term: [] } }
  const { container } = setup({ 'GET /admin/sessions/k/flows/login': { body: doc } })
  await vi.waitFor(() => found(container, '.outline [data-param="term"]'))
  expect(container.querySelector('[data-param="term"] .icon')).toHaveAttribute('aria-label', 'any type')
  expect(container.querySelector('[data-param="term"] .icon')).toHaveTextContent('·')
  await fireEvent.click(container.querySelector('.outline [data-param="term"]')!)
  const pane = container.querySelector('.pane') as HTMLElement
  expect(pane).not.toHaveTextContent('That parameter is gone.')
  expect(pane.querySelector('.dhead .chip')).toHaveTextContent('term')
  expect(pane).toHaveTextContent('optional')
  await fireEvent.click(container.querySelector('.outline [data-step="0"]')!)
  expect(pane).not.toHaveTextContent('That step is gone.')
  expect(pane.querySelector('.dhead')).toHaveTextContent('1?')
  expect(pane).toHaveTextContent('This step takes nothing.')
})

test('a refresh keeps the selection, shows no Loading…, and says a removed pick is gone (W2, W7)', async () => {
  const again = deferred<{ body: unknown }>()
  const replies = [
    () => ({ body: DOC }),
    () => again.promise,
    () => ({ body: { ...DOC, steps: [DOC.steps[0]] } }),
  ]
  let n = 0
  const { container, live } = setup({ 'GET /admin/sessions/k/flows/login': () => replies[n++]() })
  await vi.waitFor(() => found(container, '.outline [data-step="1"]'))
  await fireEvent.click(container.querySelector('.outline [data-step="1"]')!)
  // A heartbeat: the library moved.
  live.data = { sessions: [{ key: 'k', flows_rev: 2 }] }
  await vi.waitFor(() => expect(n).toBe(2))
  await tick()
  expect(container.querySelector('#flowpanel')).not.toHaveTextContent('Loading…')
  expect(container.querySelector('.pane .dhead')).toHaveTextContent('type-pass')
  again.resolve({ body: { ...DOC, steps: [DOC.steps[0], { ...DOC.steps[1], id: 'typed' }] } })
  await vi.waitFor(() => expect(container.querySelector('.pane .dhead')).toHaveTextContent('typed'))
  expect(container.querySelector('.outline [data-step="1"]')).toHaveAttribute('aria-selected', 'true')
  // The next edit removes the picked step: the pane says so, and the pick stands.
  live.data = { sessions: [{ key: 'k', flows_rev: 3 }] }
  await vi.waitFor(() => expect(container.querySelector('.pane')).toHaveTextContent('That step is gone.'))
})

test('picking a flow clears the selection, shows Loading…, and replaces the hash before the document lands (W2)', async () => {
  const shared = deferred<{ body: unknown }>()
  const { container } = setup({ 'GET /admin/sessions/k/flows/shared-one': () => shared.promise })
  await vi.waitFor(() => found(container, '.outline [data-step="1"]'))
  await fireEvent.click(container.querySelector('.outline [data-step="1"]')!)
  const pushed = vi.spyOn(history, 'pushState')
  const replaced = vi.spyOn(history, 'replaceState')
  const depth = history.length
  await fireEvent.click(container.querySelectorAll('.flowlist .item')[1])
  expect(container.querySelector('#flowpanel')).toHaveTextContent('Loading…')
  expect(location.hash).toBe('#/sessions/k/flows/shared-one')
  expect(replaced).toHaveBeenCalled()
  expect(pushed).not.toHaveBeenCalled()
  expect(history.length).toBe(depth)
  expect(container.querySelectorAll('.flowlist .item')[1]).toHaveAttribute('aria-selected', 'true')
  shared.resolve({ body: { name: 'shared-one', shared: true, steps: [{ tool: 'navigate' }] } })
  await vi.waitFor(() => found(container, '.panel'))
  expect(container.querySelector('.panel .head .pill')).toHaveTextContent('🌐 global')
  expect(container).toHaveTextContent('Pick a parameter or a step to see what it holds.')
  // Picking the open one again is still a click: its selection goes too.
  await fireEvent.click(container.querySelector('.outline [data-step="0"]')!)
  await fireEvent.click(container.querySelectorAll('.flowlist .item')[1])
  expect(container.querySelector('#flowpanel')).toHaveTextContent('Loading…')
  await vi.waitFor(() => found(container, '.panel'))
  expect(container).toHaveTextContent('Pick a parameter or a step to see what it holds.')
  pushed.mockRestore()
  replaced.mockRestore()
})

test('an error is repainted away as today: a pick reads Loading…, a listing that lands shows the document it has (W2, W7)', async () => {
  let n = 0
  const again = deferred<{ body: unknown }>()
  const held = deferred<{ body: unknown }>()
  const replies = [
    () => ({ status: 500, body: { error: 'unreadable' } }),
    () => again.promise,
    () => ({ status: 500, body: { error: 'unreadable' } }),
    () => held.promise,
  ]
  const { container, live } = setup({ 'GET /admin/sessions/k/flows/login': () => replies[n++]() })
  await vi.waitFor(() => expect(within(container.querySelector('#flowpanel')!).getByText('unreadable')).toHaveClass('empty', 'error'))
  // Picking it again.
  await fireEvent.click(container.querySelectorAll('.flowlist .item')[0])
  expect(container.querySelector('#flowpanel')).toHaveTextContent('Loading…')
  again.resolve({ body: DOC })
  await vi.waitFor(() => found(container, '.outline'))
  // A refresh that fails, then a listing that lands while the next load is out.
  live.data = { sessions: [{ key: 'k', flows_rev: 2 }] }
  await vi.waitFor(() => expect(container.querySelector('#flowpanel')).toHaveTextContent('unreadable'))
  live.data = { sessions: [{ key: 'k', flows_rev: 3 }] }
  await vi.waitFor(() => expect(n).toBe(4))
  expect(container.querySelector('#flowpanel .outline')).not.toBeNull()
  expect(container.querySelector('#flowpanel')).not.toHaveTextContent('unreadable')
})

test('a listing that failed on a refresh is repainted away by a flow opened from the hash (D5)', async () => {
  let n = 0
  const { container, live, rerender } = setup({
    'GET /admin/sessions/k/flows': () => (++n === 2 ? { status: 500, body: { error: 'boom' } } : { body: LIST }),
    'GET /admin/sessions/k/flows/shared-one': { body: { name: 'shared-one', shared: true, steps: [] } },
  })
  await vi.waitFor(() => found(container, '.outline'))
  live.data = { sessions: [{ key: 'k', flows_rev: 2 }] }
  await vi.waitFor(() => expect(container.querySelector('#flows')).toHaveTextContent('boom'))
  await rerender({ flow: 'shared-one' })
  expect(container.querySelector('#flows')).not.toHaveTextContent('boom')
  await vi.waitFor(() => expect(container.querySelector('.panel .head .nm')).toHaveTextContent('shared-one'))
})

test('a flow opened from the hash clears the selection too; it is a different document (W2, D5)', async () => {
  const { container, rerender } = setup({
    'GET /admin/sessions/k/flows/shared-one': { body: { name: 'shared-one', shared: true, steps: [{ tool: 'navigate' }] } },
  })
  await vi.waitFor(() => found(container, '.outline [data-step="0"]'))
  await fireEvent.click(container.querySelector('.outline [data-step="0"]')!)
  expect(container.querySelector('.outline [data-step="0"]')).toHaveAttribute('aria-selected', 'true')
  await rerender({ flow: 'shared-one' })
  await vi.waitFor(() => expect(container.querySelector('.panel .head .nm')).toHaveTextContent('shared-one'))
  expect(container.querySelector('.outline [data-step="0"]')).toHaveAttribute('aria-selected', 'false')
  expect(container.querySelector('.pane')).toHaveTextContent('Pick a parameter or a step to see what it holds.')
})

test('a deep-linked flow the listing does not have is never fetched or opened (D5)', async () => {
  history.replaceState(null, '', '/#/sessions/k/flows/ghost')
  const { container, calls } = setup({}, 'ghost')
  await vi.waitFor(() => expect(container.querySelectorAll('.flowlist .item')).toHaveLength(2))
  await tick()
  expect(calls.some((c) => c.path.endsWith('/flows/ghost'))).toBe(false)
  expect(container.querySelector('#flowpanel')).toHaveTextContent('Pick a flow.')
  expect(container.querySelectorAll('.flowlist .item[aria-selected="true"]')).toHaveLength(0)
})

test('Edit reads the file again, shows it raw, and saves it (W4)', async () => {
  const { container, calls } = setup({ 'PUT /admin/sessions/k/flows/login': { body: {} } })
  await vi.waitFor(() => found(container, '[data-edit]'))
  const reads = () => calls.filter((c) => c.method === 'GET' && c.path === '/admin/sessions/k/flows/login').length
  const before = reads()
  await fireEvent.click(container.querySelector('[data-edit]')!)
  const sheet = await vi.waitFor(() => found(document, '.modal'))
  expect(reads()).toBe(before + 1)
  expect(sheet.querySelector('.head')).toHaveTextContent('Edit login')
  const area = sheet.querySelector('textarea.yaml') as HTMLTextAreaElement
  expect(area).toHaveAttribute('spellcheck', 'false')
  expect(area.value).toBe('# comments survive\nsteps: []\n')
  await fireEvent.input(area, { target: { value: 'steps: [x]' } })
  await fireEvent.click(within(sheet).getByText('Save'))
  await vi.waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ yaml: 'steps: [x]' }))
  // Then the listing again, for the session it was saved in.
  await vi.waitFor(() => expect(document.querySelector('.modal')).toBeNull())
  expect(calls.filter((c) => c.path === '/admin/sessions/k/flows')).toHaveLength(2)
})

test('Edit: a failed read alerts and opens nothing; a failed save alerts and stays open (W4)', async () => {
  let reads = 0
  const { container } = setup({
    'GET /admin/sessions/k/flows/login': () => (++reads === 2 ? { status: 500, body: { error: 'gone away' } } : { body: DOC }),
    'PUT /admin/sessions/k/flows/login': { status: 400, body: { error: 'not a flow' } },
  })
  await vi.waitFor(() => found(container, '[data-edit]'))
  await fireEvent.click(container.querySelector('[data-edit]')!)
  await vi.waitFor(() => expect(alert).toHaveBeenCalledWith('Could not read that flow: gone away'))
  expect(document.querySelector('.modal')).toBeNull()
  await fireEvent.click(container.querySelector('[data-edit]')!)
  const sheet = await vi.waitFor(() => found(document, '.modal'))
  await fireEvent.click(within(sheet).getByText('Save'))
  await vi.waitFor(() => expect(alert).toHaveBeenCalledWith('not a flow'))
  expect(document.querySelector('.modal')).toBe(sheet)
})

test('Move and Delete say where, act, and close the flow (W5, W6)', async () => {
  const { container, calls } = setup({ 'POST /admin/sessions/k/flows/login/move': { body: {} }, 'DELETE /admin/sessions/k/flows/login': { body: {} } })
  await vi.waitFor(() => found(container, '[data-move]'))
  expect(container.querySelector('[data-move]')).toHaveAttribute('title', 'Move to global')
  expect(container.querySelector('[data-move]')).toHaveAttribute('aria-label', 'Move to global')
  await fireEvent.click(container.querySelector('[data-move]')!)
  const sheet = container.ownerDocument.querySelector('.modal') as HTMLElement
  expect(sheet.querySelector('.head')).toHaveTextContent('Move to global')
  expect(sheet).toHaveTextContent('It moves into the shared global library, where every session can list and run it. No agent can change what is in there — only an operator, here.')
  await fireEvent.click(within(sheet).getByText('Move'))
  await vi.waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ to: 'global' }))
  await vi.waitFor(() => expect(location.hash).toBe('#/sessions/k/flows'))
  await vi.waitFor(() => expect(container.querySelector('#flowpanel')).toHaveTextContent('Pick a flow.'))
  expect(calls.filter((c) => c.path === '/admin/sessions/k/flows')).toHaveLength(2)

  // Delete, from a reopened flow.
  await fireEvent.click(container.querySelectorAll('.flowlist .item')[0])
  await vi.waitFor(() => found(container, '[data-drop]'))
  await fireEvent.click(container.querySelector('[data-drop]')!)
  const drop = document.querySelector('.modal') as HTMLElement
  expect(drop.querySelector('.head')).toHaveTextContent('Delete this flow?')
  expect(drop).toHaveTextContent('It is removed from the folder it lives in. A flow in the global folder goes for every session, not just this one.')
  expect(drop.querySelector('ul.names li')).toHaveTextContent(/^login$/)
  expect(within(drop).getByText('Delete')).toHaveClass('danger')
  await fireEvent.click(within(drop).getByText('Delete'))
  await vi.waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.path === '/admin/sessions/k/flows/login')).toBe(true))
  await vi.waitFor(() => expect(location.hash).toBe('#/sessions/k/flows'))
  await vi.waitFor(() => expect(container.querySelector('#flowpanel')).toHaveTextContent('Pick a flow.'))
})

test('a shared flow moves home to the session, and deletes as "global / name" (W5, W6)', async () => {
  history.replaceState(null, '', '/#/sessions/k/flows/shared-one')
  const { container, calls } = setup({
    'GET /admin/sessions/k/flows/shared-one': { body: { name: 'shared-one', shared: true, steps: [] } },
    'POST /admin/sessions/k/flows/shared-one/move': { body: {} },
  }, 'shared-one')
  await vi.waitFor(() => found(container, '[data-move]'))
  const move = container.querySelector('[data-move]')!
  expect(move).toHaveAttribute('title', 'Move to this session')
  expect(move).toHaveTextContent('🏠')
  await fireEvent.click(container.querySelector('[data-drop]')!)
  expect(document.querySelector('.modal ul.names li')).toHaveTextContent('global / shared-one')
  await fireEvent.click(within(document.querySelector('.modal') as HTMLElement).getByText('Cancel'))
  await fireEvent.click(move)
  const sheet = document.querySelector('.modal') as HTMLElement
  expect(sheet.querySelector('.head')).toHaveTextContent('Move to this session')
  expect(sheet).toHaveTextContent('It moves out of the shared library into k. Other sessions stop seeing it.')
  expect(sheet.querySelector('strong')).toHaveTextContent('k')
  await fireEvent.click(within(sheet).getByText('Move'))
  await vi.waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ to: 'k' }))
})

test('there is no Move button in the global session: both ends are the same folder (W5)', async () => {
  const { container } = setup({ 'GET /admin/sessions/k/flows': { body: { ...LIST, session: 'global' } } })
  await vi.waitFor(() => found(container, '[data-edit]'))
  expect(container.querySelector('[data-move]')).toBeNull()
  expect(container.querySelector('[data-drop]')).not.toBeNull()
})

test('a malformed flow never breaks the panel (W8)', async () => {
  const { container } = setup({ 'GET /admin/sessions/k/flows/login': { body: { name: 'login', steps: {}, parameters: 1, uses: [] } } })
  await vi.waitFor(() => expect(container).toHaveTextContent('This flow takes nothing.'))
})

test('a prototype key names nothing: no tool, no type, no uses (W8)', async () => {
  const doc = {
    name: 'login',
    parameters: { properties: { toString: { type: 'constructor' } } },
    steps: [{ tool: 'constructor', args: { a: 1 } }],
    uses: {},
  }
  const { container } = setup({ 'GET /admin/sessions/k/flows/login': { body: doc } })
  await vi.waitFor(() => found(container, '.outline [data-step="0"]'))
  expect(container.querySelector('[data-step="0"] .icon')).toHaveTextContent('❓')
  expect(container.querySelector('[data-param="toString"] .icon')).toHaveTextContent('·')
  await fireEvent.click(container.querySelector('.outline [data-param="toString"]')!)
  expect(container.querySelector('.pane')).toHaveTextContent('No step reads ${toString}.')
})

test('moving from session A to B with a flow in the hash never acts on A for B (D5)', async () => {
  sessionStorage.setItem('sf-token', 't')
  vi.stubGlobal('EventSource', FakeEventSource)
  history.replaceState(null, '', '/#/sessions/a/flows/login')
  const files = (key: string) => ({ body: { session: { key, flows_rev: 1 }, downloads: [], screenshots: [], files: [] } })
  const { calls } = fakeFetch({
    'GET /admin/sessions': { body: { sessions: [], events_url: '/e' } },
    'GET /admin/sessions/a/files': files('a'),
    'GET /admin/sessions/a/flows': { body: { ...LIST, session: 'a' } },
    'GET /admin/sessions/a/flows/login': { body: DOC },
    'GET /admin/sessions/b/files': files('b'),
    'GET /admin/sessions/b/flows': { body: { ...LIST, session: 'b' } },
    'GET /admin/sessions/b/flows/shared-one': { body: { name: 'shared-one', shared: true, steps: [] } },
  })
  const { container } = render(Admin, { mount: '', console: '/grid' })
  await vi.waitFor(() => found(container, '.panel'))
  const before = calls.length
  // A flow A also has, so an old instance acting on it would find it.
  location.hash = '#/sessions/b/flows/shared-one'
  await vi.waitFor(() => expect(container.querySelector('.panel .head .nm')).toHaveTextContent('shared-one'))
  await tick()
  expect(calls.slice(before).filter((c) => c.path.startsWith('/admin/sessions/a/'))).toEqual([])
  expect(location.hash).toBe('#/sessions/b/flows/shared-one')
  sessionStorage.clear()
})
