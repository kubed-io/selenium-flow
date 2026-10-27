import { fireEvent, render, screen } from '@testing-library/svelte'
import { beforeEach, expect, test, vi } from 'vitest'
import { FakeEventSource, networkData, req, stubEventSource } from '../test/capture'
import CaptureNetworkPane from './CaptureNetworkPane.svelte'

const api = vi.fn()
beforeEach(() => { api.mockReset(); api.mockResolvedValue(networkData); stubEventSource(); history.replaceState(null, '', '#/') })
const props = () => ({ api, key: 'ws', root: '' })

test('the footers read as the boards do', async () => {
  render(CaptureNetworkPane, { props: props() })
  expect(await screen.findByText('5 requests · 19 static not kept · workspace://network')).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: /Failed/ }))
  expect(screen.getByText('2 of 5 requests · workspace://network')).toBeInTheDocument()
})

test('events capture dropped show in the Captured footer in amber, only when there were some (spotless 7)', async () => {
  api.mockResolvedValue({ ...networkData, dropped: 12 })
  const r = render(CaptureNetworkPane, { props: props() })
  expect(await screen.findByText(/12 dropped/)).toHaveClass('warn')
  r.unmount()
  api.mockResolvedValue(networkData)
  render(CaptureNetworkPane, { props: props() })
  await screen.findByText('5 requests · 19 static not kept · workspace://network')
  expect(screen.queryByText(/dropped/)).toBeNull()
})

test('a failed row shows its error where the size goes, and a row opens its request', async () => {
  render(CaptureNetworkPane, { props: props() })
  expect(await screen.findByText('net::ERR_FAILED')).toBeInTheDocument()
  await fireEvent.click(screen.getAllByText('https://a.test/x')[0])
  expect(location.hash).toMatch(/^#\/workspaces\/ws\/network\/1\.\d$/)
})

test('Clear says bodies stay fetchable', async () => {
  render(CaptureNetworkPane, { props: props() })
  await fireEvent.click(await screen.findByRole('button', { name: 'Clear' }))
  expect(screen.getByText('Requests from now on show here. Bodies the browser still holds stay fetchable from their request.')).toBeInTheDocument()
})

test('empty', async () => {
  api.mockResolvedValue({ ...networkData, requests: [], count: 0, total: 0 })
  render(CaptureNetworkPane, { props: props() })
  expect(await screen.findByText('No requests since the last navigation')).toBeInTheDocument()
})

test('a live row that was not kept does not open a request it cannot show', async () => {
  render(CaptureNetworkPane, { props: props() })
  await fireEvent.click(await screen.findByRole('button', { name: 'Stream' }))
  const es = FakeEventSource.last
  es.emit(req({ id: '2.1', seq: 20, url: 'https://a.test/logo.png', type: 'image', kept: false }))
  es.emit(req({ id: '2.2', seq: 21, url: 'https://a.test/api', kept: true }))
  await fireEvent.click(await screen.findByText('https://a.test/logo.png'))
  expect(location.hash).toBe('#/')
  await fireEvent.click(screen.getByText('https://a.test/api'))
  expect(location.hash).toBe('#/workspaces/ws/network/2.2')
})
