import { render, screen } from '@testing-library/svelte'
import { tick } from 'svelte'
import { expect, test } from 'vitest'
import { createApi } from './api'
import { Live } from './live.svelte'
import WorkspacesView from './WorkspacesView.svelte'

test('the badge is the pill while degraded and the bare dot once connected (L1)', async () => {
  const live = new Live(createApi({ base: '', token: () => 't', onUnauthorized: () => {} }), '')
  const { container } = render(WorkspacesView, { live })
  expect(container.querySelector('#live')!.getAttribute('class')).toBe('pill')
  live.badge = { text: '', cls: 'live' }
  await tick()
  expect(container.querySelector('#live')!.getAttribute('class')).toBe('dot live')
})

test('shows the badge as a bare dot while connected and words only when degraded', async () => {
  const live = new Live(createApi({ base: '', token: () => 't', onUnauthorized: () => {} }), '')
  const { container } = render(WorkspacesView, { live })
  const badge = () => container.querySelector('#live')!
  live.badge = { text: '', cls: 'live' }
  await tick()
  const dot = screen.getByRole('img', { name: 'Updates arrive as they happen' })
  expect(dot).toBe(badge())
  expect(dot.classList.contains('pill')).toBe(false)
  expect(dot.classList.contains('dot')).toBe(true)
  expect(dot.textContent!.trim()).toBe('')
  expect(dot.getAttribute('title')).toBe('Updates arrive as they happen')
  for (const text of ['connecting…', 'reconnecting…', 'polling']) {
    live.badge = { text, cls: '' }
    await tick()
    expect(badge().classList.contains('pill')).toBe(true)
    expect(badge()).toHaveTextContent(text)
    expect(badge().getAttribute('title')).toBe('Updates arrive as they happen')
    expect(badge().hasAttribute('aria-label')).toBe(false)
    expect(screen.queryByRole('img', { name: 'Updates arrive as they happen' })).toBeNull()
  }
})
