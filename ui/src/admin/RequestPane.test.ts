import { fireEvent, render, screen } from '@testing-library/svelte'
import { beforeEach, expect, test, vi } from 'vitest'
import { req } from '../test/capture'
import RequestPane from './RequestPane.svelte'

const api = vi.fn()
beforeEach(() => api.mockReset())
const detail = {
  ...req({ id: '1.1', status: 500, status_text: 'Internal Server Error', method: 'POST' }),
  uri: 'workspace://network/1.1',
  request_headers: [{ name: 'Authorization', value: '•••' }, { name: 'Accept', value: '*/*' }],
  response_headers: [{ name: 'Content-Type', value: 'text/plain' }],
}
const bodyResult = { id: '1.1', uri: 'workspace://network/1.1', mime: 'text/plain', size: 86, binary: false, text: '<i>oops</i>', truncated: false }

test('Fetch body in the admin posts to the admin route and shows the text', async () => {
  api.mockResolvedValueOnce(detail).mockResolvedValueOnce(bodyResult)
  const { container } = render(RequestPane, { props: { api, key: 'ws', id: '1.1' } })
  expect(await screen.findByText('500 Internal Server Error')).toBeInTheDocument()
  expect(screen.getByText('1 headers masked')).toBeInTheDocument()
  await fireEvent.click(await screen.findByRole('button', { name: 'Fetch body' }))
  expect(api).toHaveBeenLastCalledWith('/admin/workspaces/ws/network/1.1/body', 'POST')
  expect(await screen.findByText(/86 bytes · complete · fetched /)).toBeInTheDocument()
  expect(container.querySelector('pre')?.textContent).toBe('<i>oops</i>')
  expect(container.querySelector('i')).toBeNull()
})

test('binary and failed bodies', async () => {
  api.mockResolvedValueOnce(detail).mockResolvedValueOnce({ ...bodyResult, binary: true, text: null, mime: 'image/png', size: 2000 })
  render(RequestPane, { props: { api, key: 'ws', id: '1.1' } })
  await fireEvent.click(await screen.findByRole('button', { name: 'Fetch body' }))
  expect(await screen.findByText('image/png · 2.0 kB · not shown')).toBeInTheDocument()
})

test('a body that cannot be read says why', async () => {
  api.mockResolvedValueOnce(detail).mockRejectedValueOnce(new Error('the browser has gone'))
  render(RequestPane, { props: { api, key: 'ws', id: '1.1' } })
  await fireEvent.click(await screen.findByRole('button', { name: 'Fetch body' }))
  expect(await screen.findByText('the browser has gone')).toBeInTheDocument()
})
