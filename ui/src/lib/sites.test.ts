import { expect, test } from 'vitest'
import { siteCounts } from './sites'

const row = (cookies: number, storage: [number, number][] = []) => ({
  site: 'app.example.com',
  cookies,
  storage: storage.map(([l, s], i) => ({ origin: 'https://app.example.com:' + i, local_storage: l, session_storage: s })),
})

test('a cookie-only host reads its cookies', () => {
  expect(siteCounts(row(3))).toBe('3 cookies')
  expect(siteCounts(row(1))).toBe('1 cookie')
})

test('a host with storage gives every count, summed over its origins', () => {
  expect(siteCounts(row(2, [[2, 0]]))).toBe('2 cookies · 2 local · 0 session')
  expect(siteCounts(row(0, [[1, 1], [2, 0]]))).toBe('0 cookies · 3 local · 1 session')
})
