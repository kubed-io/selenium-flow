import { fireEvent, render, screen } from '@testing-library/svelte'
import { beforeEach, expect, test, vi } from 'vitest'
import { networkData, stubEventSource } from '../test/capture'
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
