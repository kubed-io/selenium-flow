import { expect, test } from 'vitest'
import { fakeFetch } from './helpers'

test('a no-body status resolves with an empty body instead of throwing', async () => {
  fakeFetch({ 'DELETE /flow/1': { status: 204 } })
  const res = await fetch('/flow/1', { method: 'DELETE' })
  expect(res.status).toBe(204)
  expect(await res.text()).toBe('')
})

test('an ordinary route still returns its JSON body', async () => {
  fakeFetch({ 'GET /flow': { status: 200, body: { ok: true } } })
  const res = await fetch('/flow')
  expect(res.status).toBe(200)
  expect(await res.json()).toEqual({ ok: true })
})
