import { expect, test } from 'vitest'
import { hostPath, isError, isFailed, kb, pathOf, statusText, clock } from './capture'
import type { ConsoleEntry, RequestRow } from './types'

const row = (o: Partial<RequestRow>): RequestRow => ({
  id: '1.1', seq: 1, at: 0, method: 'GET', url: 'https://a.test/x', status: 200, type: 'fetch', size: null,
  took_ms: null, failed: false, error: null, context: '', nav: 1, navigation: false, ...o,
})
const entry = (o: Partial<ConsoleEntry>): ConsoleEntry => ({
  seq: 1, at: 0, level: 'info', type: 'console', text: '', source: null, context: '', nav: 1, ...o,
})

test('kb', () => {
  expect(kb(412)).toBe('0.4 kB')
  expect(kb(8300)).toBe('8.3 kB')
  expect(kb(null)).toBe('—')
})

test('clock is a local 24 hour time', () => {
  expect(clock(new Date(2026, 9, 10, 13, 1, 43).getTime() / 1000)).toBe('13:01:43')
})

test('status text and failure', () => {
  expect(statusText(row({ failed: true, status: null }))).toBe('failed')
  expect(statusText(row({ status: 500 }))).toBe('500')
  expect(statusText(row({ status: null }))).toBe('')
  expect(isFailed(row({ status: 404 }))).toBe(true)
  expect(isFailed(row({ status: 302 }))).toBe(false)
  expect(isFailed(row({ failed: true, status: null }))).toBe(true)
})

test('an error is a level or a thrown exception', () => {
  expect(isError(entry({ level: 'error' }))).toBe(true)
  expect(isError(entry({ level: 'warn', type: 'javascript' }))).toBe(true)
  expect(isError(entry({}))).toBe(false)
})

test('host and path, no scheme', () => {
  expect(hostPath('https://a.test/api/o?page=2')).toBe('a.test/api/o?page=2')
  expect(hostPath('not a url')).toBe('not a url')
  expect(pathOf('https://a.test/api/o')).toBe('/api/o')
})
