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
  const failed = await createApi({ base: '', token: () => 't', onUnauthorized: () => {} })('/x').catch((e) => e) as ApiError
  expect(failed).toBeInstanceOf(ApiError)
  expect(failed.status).toBe(403)
  expect(failed.message).toBe('this sign-in does not hold an admin role')
})

test('a 401 the hook answers yes to is tried once more, with the bearer it holds now', async () => {
  let bearer = 'old'
  const { calls } = fakeFetch({ 'GET /x': (init) => (new Headers(init.headers).get('Authorization') === 'Bearer new' ? { body: { ok: 1 } } : { status: 401 }) })
  const onUnauthorized = vi.fn(async () => { bearer = 'new'; return true })
  expect(await createApi({ base: '', token: () => bearer, onUnauthorized })('/x')).toEqual({ ok: 1 })
  expect(onUnauthorized).toHaveBeenCalledExactlyOnceWith('old', false)
  expect(calls.map((c) => c.headers.get('Authorization'))).toEqual(['Bearer old', 'Bearer new'])
})

test('a second 401 is final: the hook hears it, and the call fails', async () => {
  const { calls } = fakeFetch({ 'GET /x': { status: 401 } })
  const onUnauthorized = vi.fn(() => true)
  await expect(createApi({ base: '', token: () => 't', onUnauthorized })('/x')).rejects.toThrow('unauthorized')
  expect(onUnauthorized.mock.calls).toEqual([['t', false], ['t', true]])
  expect(calls).toHaveLength(2)
})

test('ready is awaited before every call, and one that rejects sends nothing', async () => {
  const { calls } = fakeFetch({ 'GET /x': { body: {} } })
  let bearer = 'old'
  const ready = vi.fn(async () => { bearer = 'new' })
  await createApi({ base: '', token: () => bearer, onUnauthorized: () => {}, ready })('/x')
  expect(calls[0].headers.get('Authorization')).toBe('Bearer new')
  const ended = new ApiError('unauthorized', 401)
  await expect(createApi({ base: '', token: () => 't', onUnauthorized: () => {}, ready: async () => { throw ended } })('/x')).rejects.toBe(ended)
  expect(calls).toHaveLength(1)
})

test('a ready with nothing to wait for sends at once', () => {
  const { calls } = fakeFetch({ 'GET /x': { body: {} } })
  void createApi({ base: '', token: () => 't', onUnauthorized: () => {}, ready: () => {} })('/x')
  expect(calls).toHaveLength(1)
})
