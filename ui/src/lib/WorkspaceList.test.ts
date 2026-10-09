import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import WorkspaceList from './WorkspaceList.svelte'

const row = { key: 'k1', name: 'claudecode', browser: 'chrome', version: '140', session_id: 'abc', live: true, counts: { downloads: 0, screenshots: 2, files: 1 }, url: 'https://x.y/' }

test('No workspaces yet.', () => {
  render(WorkspaceList, { data: { workspaces: [] } })
  expect(screen.getByText('No workspaces yet.')).toBeInTheDocument()
})

test('a card: mark, name pill, id, live, meta, url (L3, frozen selector)', () => {
  const { container } = render(WorkspaceList, { data: { workspaces: [row, { key: 'k2', owner: 'stdio' }] } })
  const [card, idle] = container.querySelectorAll('.card')
  expect(card.querySelector('.bmark')).toHaveAttribute('title', 'chrome')
  expect(card.querySelector('span.pill.name')).toHaveTextContent('claudecode')
  expect(card.querySelector('.mono')).toHaveTextContent('abc')
  expect(card.querySelector('.pill.live')).toHaveTextContent('live')
  expect(card).toHaveTextContent('chrome 140 · 2 screenshots · 1 file')
  expect(card.querySelector('.url')).toHaveTextContent('https://x.y/')
  expect(idle.querySelector('.mono')).toHaveTextContent('no browser')
  expect(idle).toHaveTextContent('idle')
  expect(idle.querySelector('.pill.name')).toHaveTextContent('stdio')
})

test('picking is optional; without it the cards are plain (L3)', async () => {
  const onpick = vi.fn()
  const r1 = render(WorkspaceList, { data: { workspaces: [row] }, onpick })
  expect(r1.container.querySelector('.card')).toHaveClass('click')
  await fireEvent.click(r1.container.querySelector('.card')!)
  expect(onpick).toHaveBeenCalledWith('k1')
  r1.unmount()

  const { container } = render(WorkspaceList, { data: { workspaces: [row] } })
  const card = container.querySelector('.card')!
  expect(card).not.toHaveClass('click')
  await expect(fireEvent.click(card)).resolves.not.toThrow()
  expect(onpick).toHaveBeenCalledTimes(1)
})

test('the card class attribute is exactly "card", or "card click" when pickable (frozen selector)', () => {
  const r1 = render(WorkspaceList, { data: { workspaces: [row] }, onpick: () => {} })
  expect(r1.container.querySelector('.card')!.getAttribute('class')).toBe('card click')
  r1.unmount()
  const { container } = render(WorkspaceList, { data: { workspaces: [row] } })
  expect(container.querySelector('.card')!.getAttribute('class')).toBe('card')
})
