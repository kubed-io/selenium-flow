import { expect, test, vi } from 'vitest'
import { deferred } from '../test/helpers'
import { Latest } from './latest'

test('an overtaken load never paints, even when it answers last', async () => {
  const loads = new Latest()
  const a = deferred<string>(), b = deferred<string>()
  const paint = vi.fn()
  const first = loads.run(() => a.promise, paint)
  const second = loads.run(() => b.promise, paint)
  b.resolve('B'); await second
  a.resolve('A'); await first
  expect(paint.mock.calls).toEqual([['B']])
})

test('an overtaken load is aborted', async () => {
  const loads = new Latest()
  let signal: AbortSignal | undefined
  void loads.run((s) => { signal = s; return new Promise(() => {}) }, () => {})
  void loads.run(() => Promise.resolve(1), () => {})
  expect(signal?.aborted).toBe(true)
})

test('an overtaken failure is silent too', async () => {
  const loads = new Latest()
  const a = deferred<string>()
  const fail = vi.fn()
  const first = loads.run(() => a.promise, () => {}, fail)
  await loads.run(() => Promise.resolve('B'), () => {}, fail)
  a.reject(new Error('late')); await first
  expect(fail).not.toHaveBeenCalled()
})

test('settled() waits for the newest load, not the one it was handed (the Keep race)', async () => {
  const loads = new Latest()
  const mine = deferred<string>(), poll = deferred<string>()
  const painted: string[] = []
  void loads.run(() => mine.promise, (v) => painted.push(v))
  const settled = loads.settled()
  void loads.run(() => poll.promise, (v) => painted.push(v)) // the poll overtakes
  mine.resolve('stale')
  let done = false
  void settled.then(() => { done = true })
  await Promise.resolve(); await Promise.resolve()
  expect(done).toBe(false)
  poll.resolve('fresh'); await settled
  expect(painted).toEqual(['fresh'])
})

test('abort() stops everything in flight from painting', async () => {
  const loads = new Latest()
  const a = deferred<string>()
  const paint = vi.fn()
  const run = loads.run(() => a.promise, paint)
  loads.abort()
  a.resolve('A'); await run
  expect(paint).not.toHaveBeenCalled()
})
