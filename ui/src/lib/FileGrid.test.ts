import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import FileGrid from './FileGrid.svelte'

const png = { name: 'hangar-check.png', size: 2048, url: '/f/1', image: true, created: Date.now() - 5000 }
const pdf = { name: 'report.pdf', size: 10, url: '/f/2' }

test('an empty grid says so (F6)', () => {
  render(FileGrid, { files: [], empty: 'No screenshots yet.' })
  expect(screen.getByText('No screenshots yet.')).toHaveClass('empty')
})

test('the default empty text', () => {
  render(FileGrid, { files: [] })
  expect(screen.getByText('No files in this session yet.')).toBeInTheDocument()
})

test('a tile: thumbnail or glyph, name with an exact class, size and age (F5, frozen selector)', () => {
  const { container } = render(FileGrid, { files: [png, pdf], base: '/root' })
  const tiles = container.querySelectorAll('.files > .file')
  expect(tiles).toHaveLength(2)
  expect(tiles[0].querySelector('img')).toHaveAttribute('src', '/root/f/1')
  expect(tiles[0].querySelector('img')).toHaveAttribute('loading', 'lazy')
  expect(tiles[1].querySelector('.glyph')).toHaveTextContent('📄')
  const name = tiles[0].querySelector('.meta > div')!
  expect(name.getAttribute('class')).toBe('name')
  expect(name).toHaveTextContent('hangar-check.png')
  expect(tiles[0].querySelector('.meta .small')).toHaveTextContent('2.0 KB · 5s ago')
  expect(tiles[0].querySelector('a.thumb')).toHaveAttribute('target', '_blank')
})

test('no action, no button — the MCP App holds no credential', () => {
  const { container } = render(FileGrid, { files: [png] })
  expect(container.querySelector('button')).toBeNull()
})

test('keep: 📌 top-left, disabled for the round trip, re-armed on failure (F5)', async () => {
  let answer!: (ok: boolean) => void
  const onkeep = vi.fn(() => new Promise<boolean>((r) => { answer = r }))
  const { container } = render(FileGrid, { files: [png], action: 'keep', onkeep })
  const btn = container.querySelector('button.act.keep') as HTMLButtonElement
  expect(btn).toHaveAttribute('aria-label', 'Keep hangar-check.png beyond this browser')
  expect(btn).toHaveAttribute('title', 'Keep it beyond this browser')
  expect(btn).toHaveTextContent('📌')
  await fireEvent.click(btn)
  expect(btn).toBeDisabled()
  answer(false)
  await vi.waitFor(() => expect(btn).not.toBeDisabled())
})

test('keep: a success stays disarmed until a reload hands the tile a fresh entry (F5)', async () => {
  const onkeep = vi.fn(async () => true)
  const { container, rerender } = render(FileGrid, { files: [png], action: 'keep', onkeep })
  const btn = container.querySelector('button.act.keep') as HTMLButtonElement
  await fireEvent.click(btn)
  await vi.waitFor(() => expect(onkeep).toHaveBeenCalled())
  expect(btn).toBeDisabled()
  // The same name, a new object: what a reload of the listing delivers.
  await rerender({ files: [{ ...png }] })
  expect(container.querySelector('button.act.keep')).toBe(btn)
  expect(btn).not.toBeDisabled()
})

test('delete: 🗑 hands the file to the page', async () => {
  const ondelete = vi.fn()
  const { container } = render(FileGrid, { files: [png], action: 'delete', ondelete })
  const btn = container.querySelector('button.act.drop')!
  expect(btn).toHaveAttribute('aria-label', 'Delete hangar-check.png')
  await fireEvent.click(btn)
  expect(ondelete).toHaveBeenCalledWith(png)
})

test('a plain click opens the viewer; a modified click follows the link', async () => {
  const onopen = vi.fn()
  const { container } = render(FileGrid, { files: [pdf, png], onopen })
  const thumb = container.querySelectorAll('a.thumb')[1]
  await fireEvent.click(thumb, { ctrlKey: true })
  expect(onopen).not.toHaveBeenCalled()
  await fireEvent.click(thumb)
  expect(onopen).toHaveBeenCalledWith(1)
})

test('without onopen the grid opens its own read-only lightbox (P1)', async () => {
  const { container } = render(FileGrid, { files: [png] })
  await fireEvent.click(container.querySelector('a.thumb')!)
  expect(container.ownerDocument.querySelector('.lightbox')).not.toBeNull()
  expect(screen.queryByText('📌 Keep')).toBeNull()
})
