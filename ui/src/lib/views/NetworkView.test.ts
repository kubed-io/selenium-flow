import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import NetworkView from './NetworkView.svelte'
import type { RequestRow } from '../types'

const r = (o: Partial<RequestRow>): RequestRow => ({
  id: '1.1', seq: 1, at: 0, method: 'GET', url: 'https://the-internet.herokuapp.com/api/me', status: 200, type: 'fetch',
  size: 10, took_ms: 42, failed: false, error: null, context: '', nav: 1, navigation: false, ...o,
})
const networkData = {
  capture: true as const, pages: [], count: 3, total: 22, not_kept: { static: 19 },
  requests: [
    r({ id: '1.1', method: 'POST', url: 'https://the-internet.herokuapp.com/api/orders', status: 500, took_ms: 318 }),
    r({ id: '1.2', url: 'https://cdn.example.com/track.gif', status: null, failed: true, took_ms: null, type: 'image' }),
    r({ id: '1.3' }),
  ],
}

test('a network row drills into its request', async () => {
  const onshow = vi.fn()
  render(NetworkView, { props: { data: networkData, uri: 'workspace://network', onshow } })
  await fireEvent.click(screen.getByText('the-internet.herokuapp.com/api/orders'))
  expect(onshow).toHaveBeenCalledWith('workspace://network/1.1')
  expect(screen.getByText('19 successful static requests not shown · since the last navigation')).toBeInTheDocument()
})

test('a view show cut to its newest requests says so', () => {
  render(NetworkView, { props: { data: { ...networkData, count: 600, total: 600 } } })
  expect(screen.getByText('showing the newest 3 of 600')).toBeInTheDocument()
})

test('the chips count the listing, not the newest rows show sent (R1-7)', () => {
  render(NetworkView, { props: { data: { ...networkData, count: 600, total: 600, tally: { failed: 410, xhr: 590 } } } })
  expect(screen.getByText('Failed').nextElementSibling).toHaveTextContent('410')
  expect(screen.getByText('XHR · fetch').nextElementSibling).toHaveTextContent('590')
})

test('chips count failures and xhr/fetch; rows show status and time', () => {
  render(NetworkView, { props: { data: networkData } })
  expect(screen.getByText('Failed').nextElementSibling).toHaveTextContent('2')
  expect(screen.getByText('XHR · fetch').nextElementSibling).toHaveTextContent('2')
  expect(screen.getByText('failed')).toBeInTheDocument()
  expect(screen.getByText('500')).toBeInTheDocument()
  expect(screen.getByText('318 ms')).toBeInTheDocument()
  expect(screen.queryByRole('button')).toBeNull()
})

test('no footer without static requests; capture off says so', () => {
  const { unmount } = render(NetworkView, { props: { data: { ...networkData, not_kept: { static: 0 } } } })
  expect(screen.queryByText(/not shown/)).toBeNull()
  unmount()
  render(NetworkView, { props: { data: { capture: false, how: 'x' } } })
  expect(screen.getByText(/Capture is off for this workspace/)).toBeInTheDocument()
})
