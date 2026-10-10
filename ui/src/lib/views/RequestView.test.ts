import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import RequestView from './RequestView.svelte'
import type { RequestDetail } from '../types'

const detail: RequestDetail = {
  id: '1.1', uri: 'workspace://network/1.1', seq: 1, at: 0, method: 'POST', url: 'https://the-internet.herokuapp.com/api/orders',
  status: 500, type: 'fetch', size: 412, took_ms: 318, failed: false, error: null, context: '', nav: 1, navigation: false,
  request_headers: [{ name: 'authorization', value: '•••' }],
  response_headers: [{ name: 'content-type', value: 'application/json' }],
}
const body = { id: '1.1', uri: 'workspace://network/1.1', mime: 'application/json', size: 86, binary: false,
  text: '{"error":"orders: insert failed"}', truncated: false }

test('the request: title, uri, status and its three sections', async () => {
  const onshow = vi.fn()
  render(RequestView, { props: { data: detail, uri: 'workspace://network/1.1', onshow } })
  expect(screen.getByText('POST /api/orders')).toBeInTheDocument()
  expect(screen.getByText('workspace://network/1.1')).toBeInTheDocument()
  expect(screen.getByText('500')).toBeInTheDocument()
  expect(screen.getByText('fetch · 318 ms · 0.4 kB')).toBeInTheDocument()
  expect(screen.getByText('authorization')).toBeInTheDocument()
  expect(screen.getByText('application/json')).toBeInTheDocument()
  expect(screen.queryByText('during')).toBeNull()
  await fireEvent.click(screen.getByRole('button', { name: '← Network' }))
  expect(onshow).toHaveBeenCalledWith('workspace://network')
})

test('Fetch body calls network_body and shows what came back', async () => {
  const oncall = vi.fn().mockResolvedValue(body)
  render(RequestView, { props: { data: detail, uri: 'workspace://network/1.1', oncall } })
  expect(screen.getByText('read from the browser while it lives')).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: 'Fetch body' }))
  expect(oncall).toHaveBeenCalledWith('network_body', { id: '1.1' })
  const text = await screen.findByText('{"error":"orders: insert failed"}')
  expect(text.tagName).toBe('PRE')
})

test('a body is text, never markup', async () => {
  const oncall = vi.fn().mockResolvedValue({ ...body, text: '<img src=x onerror=alert(1)>' })
  const { container } = render(RequestView, { props: { data: detail, oncall } })
  await fireEvent.click(screen.getByRole('button', { name: 'Fetch body' }))
  await screen.findByText('<img src=x onerror=alert(1)>')
  expect(container.querySelector('img')).toBeNull()
})

test('truncated, binary and failed fetches say so', async () => {
  const oncall = vi.fn()
    .mockResolvedValueOnce({ ...body, truncated: true })
    .mockResolvedValueOnce({ ...body, binary: true, mime: 'image/png', size: 8300, text: null })
    .mockRejectedValueOnce(new Error('the browser is gone'))
  render(RequestView, { props: { data: detail, oncall } })
  const button = screen.getByRole('button', { name: 'Fetch body' })
  await fireEvent.click(button)
  expect(await screen.findByText('truncated at 100 kB')).toBeInTheDocument()
  await fireEvent.click(button)
  expect(await screen.findByText('image/png · 8.3 kB · not shown')).toBeInTheDocument()
  await fireEvent.click(button)
  expect(await screen.findByText('the browser is gone')).toBeInTheDocument()
})

test('no oncall, no Fetch body', () => {
  render(RequestView, { props: { data: detail, uri: 'workspace://network/1.1' } })
  expect(screen.queryByRole('button', { name: 'Fetch body' })).toBeNull()
})
