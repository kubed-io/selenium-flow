import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { FakeRfb, FakeSocket, MINT } from '../test/live'
import { ApiError, type Api } from './api'
import LivePane from './LivePane.svelte'

let paste: () => unknown
const api = vi.fn(async (path: string) => (path.endsWith('/paste') ? paste() : { ...MINT })) as unknown as Api

beforeEach(() => {
  FakeSocket.all = []; FakeRfb.all = []
  paste = () => ({ typed: 2 })
  vi.stubGlobal('WebSocket', FakeSocket)
})
afterEach(() => vi.unstubAllGlobals())

async function setup(load: () => Promise<{ RFB: never }> = async () => ({ RFB: FakeRfb as never }), sockets = 1) {
  const r = render(LivePane, { api, key: 'k', name: 'mine', root: '', load })
  await vi.waitFor(() => expect(FakeSocket.all).toHaveLength(sockets))
  return r
}
const sock = () => FakeSocket.all.at(-1)!
const connect = async () => {
  FakeRfb.all.at(-1)!.dispatchEvent(new Event('connect'))
  await vi.waitFor(() => expect(screen.getByText('● connected')).toBeInTheDocument())
}
const control = async () => {
  await connect()
  await fireEvent.click(screen.getByRole('button', { name: /Control/ }))
  sock().say('{"type":"mode","mode":"control"}')
  await vi.waitFor(() => expect(screen.getByRole('button', { name: /Control/ })).toHaveAttribute('aria-pressed', 'true'))
}

test('connecting reads connecting, with the controls dimmed', async () => {
  const { container } = await setup()
  expect(screen.getByText('connecting')).toHaveClass('pill')
  expect(container.querySelector('.livepane .controls')).toHaveClass('dim')
  expect(container.querySelector('.screen')).toHaveClass('connecting')
})

test('the paste button is greyed in Watch with its tooltip', async () => {
  await setup(); await connect()
  const btn = screen.getByRole('button', { name: /Paste/ })
  expect(btn).toBeDisabled()
  expect(btn).toHaveAttribute('title', 'Paste types into the browser in Control')
  expect(screen.getByRole('button', { name: /Watch/ })).toHaveAttribute('aria-pressed', 'true')
})

test('Control gets the outline and opens the paste popover', async () => {
  const { container } = await setup(); await control()
  expect(container.querySelector('.screen')).toHaveClass('control')
  await fireEvent.click(screen.getByRole('button', { name: /Paste/ }))
  expect(screen.getByText('Types at the cursor in the browser')).toBeInTheDocument()
  expect(screen.getByPlaceholderText('Paste')).toBeInTheDocument()
})

test('a refused paste keeps its text and shows the sentence; a sent one closes the popover', async () => {
  await setup(); await control()
  await fireEvent.click(screen.getByRole('button', { name: /Paste/ }))
  const box = screen.getByPlaceholderText('Paste') as HTMLTextAreaElement
  await fireEvent.input(box, { target: { value: 'secret text' } })
  paste = () => { throw new ApiError('this browser is not in Control', 409) }
  await fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  expect(await screen.findByText('this browser is not in Control')).toBeInTheDocument()
  expect(box.value).toBe('secret text')
  paste = () => ({ typed: 11 })
  await fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await vi.waitFor(() => expect(screen.queryByPlaceholderText('Paste')).toBeNull())
})

test('disconnected shows the reason, dims, and Reconnect mints again', async () => {
  const { container } = await setup(); await connect()
  sock().shut(4001, 'idle')
  const pill = await screen.findByText('disconnected')
  expect(pill).toHaveClass('warn')
  expect(pill).toHaveAttribute('title', 'idle')
  expect(container.querySelector('.livepane .controls')).toHaveClass('dim')
  await fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }))
  await vi.waitFor(() => expect(FakeSocket.all).toHaveLength(2))
  expect(screen.queryByRole('button', { name: 'Reconnect' })).toBeNull()
})

test('fullscreen asks the pane, shows the head row and Close leaves it', async () => {
  const { container } = await setup(); await connect()
  const pane = container.querySelector('.livepane') as HTMLElement
  let full: Element | null = null
  pane.requestFullscreen = vi.fn(async () => { full = pane; document.dispatchEvent(new Event('fullscreenchange')) })
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => full })
  document.exitFullscreen = vi.fn(async () => { full = null; document.dispatchEvent(new Event('fullscreenchange')) })
  await fireEvent.click(screen.getByRole('button', { name: 'Fullscreen' }))
  expect(pane.requestFullscreen).toHaveBeenCalled()
  await vi.waitFor(() => expect(pane).toHaveClass('full'))
  expect(screen.getByText('mine')).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  expect(document.exitFullscreen).toHaveBeenCalled()
  await vi.waitFor(() => expect(pane).not.toHaveClass('full'))
})

test('when the viewer cannot load it says so', async () => {
  const { container } = await setup(async () => { throw new Error('404') }, 0)
  expect(await screen.findByText('no live view · this page could not load the viewer')).toBeInTheDocument()
  expect(container.querySelector('.livebar')).toBeNull()
  expect(container.querySelector('.screenbox')).toBeNull()
  expect(screen.queryByText('connecting')).toBeNull()
})

test('a drop leaves Control: the outline goes and paste is greyed again', async () => {
  const { container } = await setup(); await control()
  sock().shut(1006, '')
  await screen.findByText('disconnected')
  expect(container.querySelector('.screen')).not.toHaveClass('control')
  expect(screen.getByRole('button', { name: /Paste/ })).toBeDisabled()
})

test('closing the popover by an outside click forgets the refusal', async () => {
  await setup(); await control()
  await fireEvent.click(screen.getByRole('button', { name: /Paste/ }))
  await fireEvent.input(screen.getByPlaceholderText('Paste'), { target: { value: 'x' } })
  paste = () => { throw new ApiError('nope', 409) }
  await fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await screen.findByText('nope')
  await fireEvent.click(document.body)
  expect(screen.queryByPlaceholderText('Paste')).toBeNull()
  await fireEvent.click(screen.getByRole('button', { name: /Paste/ }))
  expect(screen.queryByText('nope')).toBeNull()
})

test('leaving Control closes the popover, and it stays closed on return', async () => {
  await setup(); await control()
  await fireEvent.click(screen.getByRole('button', { name: /Paste/ }))
  sock().say('{"type":"mode","mode":"watch"}')
  await vi.waitFor(() => expect(screen.queryByPlaceholderText('Paste')).toBeNull())
  await fireEvent.click(screen.getByRole('button', { name: /Control/ }))
  sock().say('{"type":"mode","mode":"control"}')
  await vi.waitFor(() => expect(screen.getByRole('button', { name: /Paste/ })).toBeEnabled())
  expect(screen.queryByPlaceholderText('Paste')).toBeNull()
})

test('Send needs some text', async () => {
  await setup(); await control()
  await fireEvent.click(screen.getByRole('button', { name: /Paste/ }))
  expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
})

test('a refused fullscreen is not an unhandled rejection', async () => {
  const { container } = await setup(); await connect()
  const pane = container.querySelector('.livepane') as HTMLElement
  pane.requestFullscreen = vi.fn(async () => { throw new TypeError('denied') })
  await fireEvent.click(screen.getByRole('button', { name: 'Fullscreen' }))
  await new Promise((r) => setTimeout(r, 0))
  expect(pane).not.toHaveClass('full')
})

test('noVNC is told to draw transparent, and the screen goes dark once connected', async () => {
  const { container } = await setup()
  expect(FakeRfb.all[0].background).toBe('transparent')
  expect(container.querySelector('.screen')).not.toHaveClass('connected')
  await connect()
  expect(container.querySelector('.screen')).toHaveClass('connected')
})
