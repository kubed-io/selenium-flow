import { expect, test, vi } from 'vitest'
import { callable } from './callable'

test('only network_body reaches the host', async () => {
  const send = vi.fn().mockResolvedValue({ structuredContent: { id: '1' } })
  const call = callable(send)
  expect(await call('network_body', { id: '1' })).toEqual({ id: '1' })
  expect(send).toHaveBeenCalledWith({ name: 'network_body', arguments: { id: '1' } })
})

test('any other tool is refused and never reaches the host', async () => {
  const send = vi.fn()
  await expect(callable(send)('execute_script', { script: 'x' })).rejects.toThrow(/execute_script/)
  await expect(callable(send)('constructor', {})).rejects.toThrow()
  expect(send).not.toHaveBeenCalled()
})

test('a refused result becomes an error with the server text', async () => {
  const send = vi.fn().mockResolvedValue({ isError: true, content: [{ type: 'text', text: 'gone' }] })
  await expect(callable(send)('network_body', {})).rejects.toThrow('gone')
})
