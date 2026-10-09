import { expect, test, vi } from 'vitest'
import { fakeFetch } from '../test/helpers'
import { ApiError, createApi, workspacePath } from './api'

test('every call carries the bearer token and hangs off BASE', async () => {
  const { calls } = fakeFetch({ 'GET /flow/admin/workspaces': { body: { workspaces: [] } } })
  const api = createApi({ base: '/flow', token: () => 'tok', onUnauthorized: () => {} })
  expect(await api('/admin/workspaces')).toEqual({ workspaces: [] })
  expect(calls[0].headers.get('Authorization')).toBe('Bearer tok')
})

test('a body is JSON with its content type', async () => {
  const { calls } = fakeFetch({ 'PUT /x': { body: {} } })
  await createApi({ base: '', token: () => 't', onUnauthorized: () => {} })('/x', 'PUT', { yaml: 'a: 1' })
  expect(calls[0].body).toEqual({ yaml: 'a: 1' })
  expect(calls[0].headers.get('Content-Type')).toBe('application/json')
})

test('a 401 signs out and fails the call (A4)', async () => {
  fakeFetch({ 'GET /x': { status: 401 } })
  const onUnauthorized = vi.fn()
  await expect(createApi({ base: '', token: () => 't', onUnauthorized })('/x')).rejects.toThrow('unauthorized')
  expect(onUnauthorized).toHaveBeenCalledOnce()
})

test("a failure says the server's own words, else the status (A5)", async () => {
  fakeFetch({ 'GET /a': { status: 400, body: { error: "the shared 'global' library is read-only" } }, 'GET /b': { status: 502 } })
  const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })
  await expect(api('/a')).rejects.toThrow("the shared 'global' library is read-only")
  await expect(api('/b')).rejects.toThrow('request failed (502)')
})

test('workspacePath encodes the key', () => {
  expect(workspacePath('a b/c', '/files')).toBe('/admin/workspaces/a%20b%2Fc/files')
})

test('a refusal carries its status, so a 403 can be told from the rest', async () => {
  fakeFetch({ 'GET /x': { status: 403, body: { error: 'this sign-in does not hold an admin role' } } })
  const failed = await createApi({ base: '', token: () => 't', onUnauthorized: () => {} })('/x').catch((e) => e)
  expect(failed).toBeInstanceOf(ApiError)
  expect(failed.status).toBe(403)
  expect(failed.message).toBe('this sign-in does not hold an admin role')
})
