import { expect, test, vi } from 'vitest'

test('durations drop to zero under prefers-reduced-motion', async () => {
  vi.stubGlobal('matchMedia', (q: string) => ({ matches: q === '(prefers-reduced-motion: reduce)' }))
  vi.resetModules()
  const { ms } = await import('./motion')
  expect(ms(150)).toBe(0)
})

test('and are capped at 150 ms otherwise', async () => {
  vi.stubGlobal('matchMedia', () => ({ matches: false }))
  vi.resetModules()
  const { ms } = await import('./motion')
  expect(ms(120)).toBe(120)
  expect(ms(400)).toBe(150)
})
