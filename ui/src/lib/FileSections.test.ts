import { fireEvent, render } from '@testing-library/svelte'
import { expect, test } from 'vitest'
import FileSections from './FileSections.svelte'

test('three titled rows with counts, in order, read-only (P1)', () => {
  const { container } = render(FileSections, { data: { downloads: [], screenshots: [{ name: 's.png', size: 1, url: '/s', image: true }], files: [] } })
  const rows = [...container.querySelectorAll('section.row')]
  expect(rows.map((r) => r.querySelector('strong')!.textContent)).toEqual(['Downloads', 'Screenshots', 'Files'])
  expect(rows.map((r) => r.querySelector('.pill')!.textContent)).toEqual(['0', '1', '0'])
  expect(rows[2]).toHaveTextContent('Nothing here yet — prints land here, and anything you keep.')
  expect(container.querySelector('button')).toBeNull()
})

test('a signed absolute_url replaces the relative one; without it the relative url stands (Copilot, #42)', async () => {
  const withAbs = { name: 'a.png', size: 1, url: '/f/a', absolute_url: 'https://host.example/f/a', image: true }
  const noAbs = { name: 'b.png', size: 1, url: '/f/b', image: true }
  const { container } = render(FileSections, { data: { downloads: [withAbs, noAbs], screenshots: [], files: [] } })
  const tiles = container.querySelectorAll('section.row')[0].querySelectorAll('.files > .file')

  const absThumb = tiles[0].querySelector('a.thumb')!
  expect(absThumb).toHaveAttribute('href', 'https://host.example/f/a')
  expect(tiles[0].querySelector('img')).toHaveAttribute('src', 'https://host.example/f/a')

  const relThumb = tiles[1].querySelector('a.thumb')!
  expect(relThumb).toHaveAttribute('href', '/f/b')
  expect(tiles[1].querySelector('img')).toHaveAttribute('src', '/f/b')

  // The lightbox opened from a tile carries the same URL it was rendered with.
  await fireEvent.click(absThumb)
  const lightbox = container.ownerDocument.querySelector('.lightbox')!
  expect(lightbox.querySelector('img')).toHaveAttribute('src', 'https://host.example/f/a')
})
