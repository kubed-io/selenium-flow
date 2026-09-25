import { render } from '@testing-library/svelte'
import { tick } from 'svelte'
import { expect, test } from 'vitest'
import { createApi } from './api'
import { Live } from './live.svelte'
import SessionsView from './SessionsView.svelte'

test('the live badge class attribute is exactly "pill", or "pill live" (L1)', async () => {
  const live = new Live(createApi({ base: '', token: () => 't', onUnauthorized: () => {} }), '')
  const { container } = render(SessionsView, { live })
  expect(container.querySelector('#live')!.getAttribute('class')).toBe('pill')
  live.badge = { text: 'live', cls: 'live' }
  await tick()
  expect(container.querySelector('#live')!.getAttribute('class')).toBe('pill live')
})
