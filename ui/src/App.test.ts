import { fireEvent, render, screen } from '@testing-library/svelte'
import { beforeEach, expect, test, vi } from 'vitest'

const host = vi.hoisted(() => ({
  listeners: {} as Record<string, (r: unknown) => void>,
  fail: null as Error | null,
  gate: null as Promise<void> | null,
  removed: [] as string[],
  close: vi.fn(),
  options: undefined as unknown,
  caps0: undefined as unknown,
  caps: { serverTools: {} } as Record<string, unknown> | undefined,
  ctx: {} as Record<string, unknown> | undefined,
  callServerTool: vi.fn(),
  updateModelContext: vi.fn(),
  requestDisplayMode: vi.fn(),
  applyDocumentTheme: vi.fn(),
  applyHostStyleVariables: vi.fn(),
  applyHostFonts: vi.fn(),
}))
vi.mock('@modelcontextprotocol/ext-apps', () => ({
  App: class {
    constructor(public info: { name: string; version: string }, caps?: unknown, options?: unknown) { host.caps0 = caps; host.options = options }
    addEventListener(event: string, fn: (r: unknown) => void) { host.listeners[event] = fn }
    removeEventListener(event: string, fn: (r: unknown) => void) { if (host.listeners[event] === fn) host.removed.push(event) }
    async connect() { if (host.gate) await host.gate; if (host.fail) throw host.fail }
    close() { return host.close() }
    getHostCapabilities() { return host.caps }
    getHostContext() { return host.ctx }
    callServerTool(p: unknown) { return host.callServerTool(p) }
    updateModelContext(p: unknown) { return host.updateModelContext(p) }
    requestDisplayMode(p: unknown) { return host.requestDisplayMode(p) }
  },
  applyDocumentTheme: (t: unknown) => host.applyDocumentTheme(t),
  applyHostStyleVariables: (v: unknown) => host.applyHostStyleVariables(v),
  applyHostFonts: (f: unknown) => host.applyHostFonts(f),
}))
import App from './App.svelte'

const FLOWS = {
  component: 'flows', uri: 'flow://flows',
  data: { session: 's', count: 1, flows: [{ name: 'login', description: 'Sign in', parameters: {}, step_count: 2, shared: false }] },
}
const FLOW = {
  component: 'flow', uri: 'flow://flows/login',
  data: { name: 'login', description: 'Sign in', parameters: {}, steps: [{ tool: 'navigate', args: { url: 'https://example.com/' } }] },
}

beforeEach(() => {
  host.listeners = {}
  host.fail = null
  host.gate = null
  host.removed = []
  host.close.mockReset()
  host.close.mockResolvedValue(undefined)
  host.options = undefined
  host.caps0 = undefined
  host.caps = { serverTools: {} }
  host.ctx = {}
  for (const fn of [host.callServerTool, host.updateModelContext, host.requestDisplayMode, host.applyDocumentTheme, host.applyHostStyleVariables, host.applyHostFonts]) fn.mockReset()
  host.updateModelContext.mockResolvedValue({})
  document.documentElement.style.height = ''
})

async function shown(result: unknown) {
  await vi.waitFor(() => expect(host.listeners.toolresult).toBeTypeOf('function'))
  host.listeners.toolresult({ structuredContent: result })
}

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}

const contexts = () => host.updateModelContext.mock.calls.map(([p]) => (p as { content: { text: string }[] }).content[0].text)

test.each([
  ['context', { session: 'drk', url: 'https://example.com/', live: true }, 'drk'],
  ['files', { session: 's', count: 0, files: [], folders: [{ name: 'screenshots', uri: 'session://files/screenshots', count: 2 }] }, 'Screenshots'],
  ['folder', { session: 's', folder: 'downloads', uri: 'session://files/downloads', count: 0, files: [] }, 'Downloads'],
  ['flows', FLOWS.data, 'login'],
  ['flow', FLOW.data, 'navigate'],
  ['secrets', { session: 's', count: 1, secrets: [{ name: 'demo', keys: ['password'], restricted: false }] }, 'demo'],
])('draws the %s view from a show result', async (component, data, text) => {
  render(App)
  expect(screen.getByText('Loading…')).toBeInTheDocument()
  await shown({ component, uri: 'x://y', data })
  await vi.waitFor(() => expect(screen.getByText(text)).toBeInTheDocument())
})

test('fileSections is no longer drawn', async () => {
  render(App)
  await shown({ component: 'fileSections', downloads: [], screenshots: [], files: [] })
  await vi.waitFor(() => expect(screen.getByText('Nothing to show for "fileSections".')).toHaveClass('error'))
})

test.each(['sessionList', 'sessionSummary'])('%s is not a view: no tool emits it', async (component) => {
  render(App)
  await shown({ component, data: { sessions: [] } })
  await vi.waitFor(() => expect(screen.getByText(`Nothing to show for "${component}".`)).toHaveClass('error'))
})

test("the model's own show call that failed draws the refusal", async () => {
  render(App)
  await vi.waitFor(() => expect(host.listeners.toolresult).toBeTypeOf('function'))
  host.listeners.toolresult({ isError: true, content: [{ type: 'text', text: 'x://y has no view; show draws session://current' }] })
  await vi.waitFor(() => expect(screen.getByText('x://y has no view; show draws session://current')).toHaveClass('error'))
  expect(screen.queryByText(/Nothing to show/)).toBeNull()
  expect(screen.queryByText('Loading…')).toBeNull()
  // The next result replaces it.
  await shown(FLOWS)
  await screen.findByRole('button', { name: 'login' })
  expect(screen.queryByText(/has no view/)).toBeNull()
})

test('a failed show with no text still says it failed', async () => {
  render(App)
  await vi.waitFor(() => expect(host.listeners.toolresult).toBeTypeOf('function'))
  host.listeners.toolresult({ isError: true, content: [] })
  await vi.waitFor(() => expect(screen.getByText('Could not show this.')).toHaveClass('error'))
})

test('the model context count is pluralised', async () => {
  render(App)
  await shown(FLOWS)
  await vi.waitFor(() => expect(contexts()).toHaveLength(1))
  expect(contexts()[0]).toBe('Showing flow://flows, 1 flow')
})

test('an unknown component says so (P1)', async () => {
  render(App)
  await shown({ component: 'nope' })
  await vi.waitFor(() => expect(screen.getByText('Nothing to show for "nope".')).toHaveClass('error'))
})

test('a host that cannot start the app says why (P1)', async () => {
  host.fail = new Error('no parent window')
  render(App)
  await vi.waitFor(() => expect(screen.getByText('This host could not start the app: no parent window')).toBeInTheDocument())
})

test('a card drills into show, Back returns and goes away', async () => {
  host.callServerTool.mockResolvedValue({ content: [], structuredContent: FLOW })
  render(App)
  await shown(FLOWS)
  await fireEvent.click(await screen.findByRole('button', { name: 'login' }))
  expect(host.callServerTool).toHaveBeenCalledWith({ name: 'show', arguments: { uri: 'flow://flows/login' } })
  await vi.waitFor(() => expect(screen.getByText('navigate')).toBeInTheDocument())
  await fireEvent.click(screen.getByRole('button', { name: 'Back' }))
  await vi.waitFor(() => expect(screen.getByRole('button', { name: 'login' })).toBeInTheDocument())
  expect(screen.queryByRole('button', { name: 'Back' })).toBeNull()
})

test('the first view has no Back', async () => {
  render(App)
  await shown(FLOWS)
  await screen.findByRole('button', { name: 'login' })
  expect(screen.queryByRole('button', { name: 'Back' })).toBeNull()
})

test('the model is told what is showing, on every change', async () => {
  host.callServerTool.mockResolvedValue({ content: [], structuredContent: FLOW })
  render(App)
  await shown(FLOWS)
  await vi.waitFor(() => expect(contexts()).toHaveLength(1))
  expect(contexts()[0]).toMatch(/^Showing flow:\/\/flows(?!\/)/)
  await fireEvent.click(await screen.findByRole('button', { name: 'login' }))
  await vi.waitFor(() => expect(contexts()).toHaveLength(2))
  expect(contexts()[1]).toMatch(/^Showing flow:\/\/flows\/login\b/)
  await fireEvent.click(await screen.findByRole('button', { name: 'Back' }))
  await vi.waitFor(() => expect(contexts()).toHaveLength(3))
  expect(contexts()[2]).toMatch(/^Showing flow:\/\/flows(?!\/)/)
  expect(host.updateModelContext.mock.calls[0][0]).toEqual({ content: [{ type: 'text', text: contexts()[0] }] })
})

test('a host that will not take model context is ignored', async () => {
  host.updateModelContext.mockRejectedValue(new Error('unsupported'))
  render(App)
  await shown(FLOWS)
  await screen.findByRole('button', { name: 'login' })
  await vi.waitFor(() => expect(host.updateModelContext).toHaveBeenCalled())
  expect(screen.queryByText(/unsupported/)).toBeNull()
})

test('a failed drill-down says so above the view it leaves in place', async () => {
  host.callServerTool.mockResolvedValueOnce({ isError: true, content: [{ type: 'text', text: 'No flow named login.' }] })
  host.callServerTool.mockRejectedValueOnce(new Error('host went away'))
  render(App)
  await shown(FLOWS)
  await fireEvent.click(await screen.findByRole('button', { name: 'login' }))
  await vi.waitFor(() => expect(screen.getByText('No flow named login.')).toHaveClass('error'))
  expect(screen.getByRole('button', { name: 'login' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Back' })).toBeNull()
  await fireEvent.click(screen.getByRole('button', { name: 'login' }))
  await vi.waitFor(() => expect(screen.getByText('host went away')).toHaveClass('error'))
  expect(screen.queryByText('No flow named login.')).toBeNull()
  expect(screen.getByRole('button', { name: 'login' })).toBeInTheDocument()
})

test('without serverTools the cards draw but do not drill', async () => {
  host.caps = {}
  render(App)
  await shown(FLOWS)
  await vi.waitFor(() => expect(screen.getByText('login')).toBeInTheDocument())
  expect(screen.queryByRole('button')).toBeNull()
})

test('fullscreen is offered on a flow only when the host has it', async () => {
  host.ctx = { availableDisplayModes: ['inline', 'fullscreen'] }
  host.requestDisplayMode.mockResolvedValue({ mode: 'fullscreen' })
  const steps = Array.from({ length: 8 }, (_, i) => ({ tool: 'navigate', args: { url: `https://example.com/${i}` } }))
  host.callServerTool.mockResolvedValue({ content: [], structuredContent: { ...FLOW, data: { ...FLOW.data, steps } } })
  const { container } = render(App)
  await shown(FLOWS)
  await screen.findByRole('button', { name: 'login' })
  expect(screen.queryByRole('button', { name: 'Fullscreen' })).toBeNull()
  await fireEvent.click(screen.getByRole('button', { name: 'login' }))
  await vi.waitFor(() => expect(screen.getByText('+2 more')).toBeInTheDocument())
  await fireEvent.click(screen.getByRole('button', { name: 'Fullscreen' }))
  expect(host.requestDisplayMode).toHaveBeenCalledWith({ mode: 'fullscreen' })
  await vi.waitFor(() => expect(container.querySelectorAll('ol li')).toHaveLength(8))
  expect(screen.queryByText('+2 more')).toBeNull()
  host.requestDisplayMode.mockResolvedValue({ mode: 'inline' })
  await fireEvent.click(screen.getByRole('button', { name: 'Exit fullscreen' }))
  expect(host.requestDisplayMode).toHaveBeenLastCalledWith({ mode: 'inline' })
  await vi.waitFor(() => expect(screen.getByText('+2 more')).toBeInTheDocument())
})

test('the mode the host grants is the one used', async () => {
  host.ctx = { availableDisplayModes: ['inline', 'fullscreen'] }
  host.requestDisplayMode.mockResolvedValue({ mode: 'inline' })
  render(App)
  await shown({ ...FLOW, data: { ...FLOW.data, steps: Array(8).fill(FLOW.data.steps[0]) } })
  await fireEvent.click(await screen.findByRole('button', { name: 'Fullscreen' }))
  await vi.waitFor(() => expect(host.requestDisplayMode).toHaveBeenCalled())
  expect(screen.getByText('+2 more')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Fullscreen' })).toBeInTheDocument()
})

test('no fullscreen button when the host does not offer it', async () => {
  host.ctx = { availableDisplayModes: ['inline'] }
  render(App)
  await shown(FLOW)
  await screen.findByText('login')
  expect(screen.queryByRole('button', { name: 'Fullscreen' })).toBeNull()
})

test('the host theme is applied on connect and on change', async () => {
  host.ctx = { theme: 'dark', styles: { variables: { '--color-text-primary': '#eee' }, css: { fonts: '@font-face{}' } } }
  render(App)
  await vi.waitFor(() => expect(host.applyDocumentTheme).toHaveBeenCalledWith('dark'))
  expect(host.applyHostStyleVariables).toHaveBeenCalledWith({ '--color-text-primary': '#eee' })
  expect(host.applyHostFonts).toHaveBeenCalledWith('@font-face{}')
  host.listeners.hostcontextchanged({ theme: 'light' })
  expect(host.applyDocumentTheme).toHaveBeenLastCalledWith('light')
})

test('the shell resizes itself and sets its own height', async () => {
  vi.spyOn(document.documentElement, 'getBoundingClientRect').mockReturnValue({ height: 321.4 } as DOMRect)
  render(App)
  expect(host.options).toMatchObject({ autoResize: true })
  await shown(FLOWS)
  await vi.waitFor(() => expect(document.documentElement.style.height).toBe('322px'))
})

test('one drill-down at a time: a second click while waiting is ignored', async () => {
  const later = deferred<unknown>()
  host.callServerTool.mockReturnValue(later.promise)
  const { container } = render(App)
  await shown(FLOWS)
  const card = await screen.findByRole('button', { name: 'login' })
  await fireEvent.click(card)
  await fireEvent.click(card)
  expect(host.callServerTool).toHaveBeenCalledTimes(1)
  await vi.waitFor(() => expect(container.querySelector('[aria-busy=true]')).not.toBeNull())
  later.resolve({ content: [], structuredContent: FLOW })
  await vi.waitFor(() => expect(screen.getByText('navigate')).toBeInTheDocument())
  expect(container.querySelector('[aria-busy=true]')).toBeNull()
  await fireEvent.click(screen.getByRole('button', { name: 'Back' }))
  await vi.waitFor(() => expect(screen.getByRole('button', { name: 'login' })).toBeInTheDocument())
  expect(screen.queryByRole('button', { name: 'Back' })).toBeNull()
})

test('a tool result that arrives during a drill-down wins over it', async () => {
  const later = deferred<unknown>()
  host.callServerTool.mockReturnValue(later.promise)
  const { container } = render(App)
  await shown(FLOWS)
  await fireEvent.click(await screen.findByRole('button', { name: 'login' }))
  host.listeners.toolresult({ structuredContent: { component: 'context', uri: 'session://current', data: { session: 'drk', live: true } } })
  await vi.waitFor(() => expect(screen.getByText('drk')).toBeInTheDocument())
  expect(container.querySelector('[aria-busy=true]')).toBeNull()
  later.resolve({ content: [], structuredContent: FLOW })
  await new Promise((r) => setTimeout(r, 20))
  expect(screen.getByText('drk')).toBeInTheDocument()
  expect(screen.queryByText('navigate')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Back' })).toBeNull()
})

test('a late drill-down failure is dropped too', async () => {
  const later = deferred<unknown>()
  host.callServerTool.mockReturnValue(later.promise)
  render(App)
  await shown(FLOWS)
  await fireEvent.click(await screen.findByRole('button', { name: 'login' }))
  host.listeners.toolresult({ structuredContent: FLOWS })
  later.resolve({ isError: true, content: [{ type: 'text', text: 'Too late.' }] })
  await new Promise((r) => setTimeout(r, 20))
  expect(screen.queryByText('Too late.')).toBeNull()
  await fireEvent.click(screen.getByRole('button', { name: 'login' }))
  expect(host.callServerTool).toHaveBeenCalledTimes(2)
})

test('unmounting lets go of the host', async () => {
  const { unmount } = render(App)
  await vi.waitFor(() => expect(host.listeners.toolresult).toBeTypeOf('function'))
  await Promise.resolve()
  unmount()
  expect(host.removed.sort()).toEqual(['hostcontextchanged', 'toolresult'])
  expect(host.close).toHaveBeenCalled()
})

test('a connect that finishes after unmount does nothing', async () => {
  const gate = deferred<void>()
  host.gate = gate.promise
  host.ctx = { theme: 'dark' }
  const { unmount } = render(App)
  await vi.waitFor(() => expect(host.listeners.toolresult).toBeTypeOf('function'))
  unmount()
  gate.resolve()
  await new Promise((r) => setTimeout(r, 20))
  expect(host.applyDocumentTheme).not.toHaveBeenCalled()
})

test('the app declares the display modes it asks for', async () => {
  render(App)
  await vi.waitFor(() => expect(host.listeners.toolresult).toBeTypeOf('function'))
  expect(host.caps0).toMatchObject({ availableDisplayModes: ['inline', 'fullscreen'] })
})

test('a fresh show result starts its view fresh', async () => {
  render(App)
  const secrets = (names: string[]) => ({
    component: 'secrets',
    uri: 'secret://secrets',
    data: { session: 's', count: names.length, secrets: names.map((name) => ({ name, keys: ['password'], restricted: false })) },
  })
  await shown(secrets(['alpha', 'beta']))
  await fireEvent.click(await screen.findByRole('button', { name: /alpha/ }))
  expect(screen.getByRole('button', { name: '← All secrets' })).toBeInTheDocument()
  await shown(secrets(['gamma', 'delta', 'epsilon']))
  await vi.waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(3))
  expect(screen.queryByRole('button', { name: '← All secrets' })).not.toBeInTheDocument()
})
