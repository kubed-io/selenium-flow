import { render, screen } from '@testing-library/svelte'
import { beforeEach, expect, test, vi } from 'vitest'

const host = vi.hoisted(() => ({ listeners: {} as Record<string, (r: unknown) => void>, fail: null as Error | null }))
vi.mock('@modelcontextprotocol/ext-apps', () => ({
  App: class {
    constructor(public info: { name: string; version: string }) {}
    addEventListener(event: string, fn: (r: unknown) => void) { host.listeners[event] = fn }
    async connect() { if (host.fail) throw host.fail }
  },
}))
import App from './App.svelte'

beforeEach(() => { host.listeners = {}; host.fail = null })

test('renders the component a tool result names (P1)', async () => {
  const { container } = render(App)
  expect(screen.getByText('Loading…')).toBeInTheDocument()
  await vi.waitFor(() => expect(host.listeners.toolresult).toBeTypeOf('function'))
  host.listeners.toolresult({ structuredContent: { component: 'fileSections', downloads: [], screenshots: [], files: [] } })
  await vi.waitFor(() => expect(container.querySelectorAll('section.row')).toHaveLength(3))
})

test('an unknown component says so (P1)', async () => {
  render(App)
  await vi.waitFor(() => expect(host.listeners.toolresult).toBeTypeOf('function'))
  host.listeners.toolresult({ structuredContent: { component: 'nope' } })
  await vi.waitFor(() => expect(screen.getByText('Nothing to show for "nope".')).toHaveClass('error'))
})

test('a host that cannot start the app says why (P1)', async () => {
  host.fail = new Error('no parent window')
  render(App)
  await vi.waitFor(() => expect(screen.getByText('This host could not start the app: no parent window')).toBeInTheDocument())
})
