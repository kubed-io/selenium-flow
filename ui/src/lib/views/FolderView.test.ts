import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test } from 'vitest'
import FolderView from './FolderView.svelte'

const f = (name: string, image = true) => ({ name, size: 10, url: 'https://example.com/f/' + name, image })

test('title, count, one horizontal row of tiles; an image tile opens the lightbox', async () => {
  const { container } = render(FolderView, { props: { data: { session: 's', folder: 'screenshots', uri: 'u', count: 2, files: [f('a.png'), f('b.png')] } } })
  expect(container.querySelector('strong')).toHaveTextContent('Screenshots')
  expect(container.querySelector('.pill')).toHaveTextContent('2')
  expect(container.querySelectorAll('.files.strip > .file')).toHaveLength(2)
  // Not the admin's flex row, which would squeeze the strip into one column.
  expect(container.querySelector('section')).not.toHaveClass('row')
  await fireEvent.click(container.querySelector('a.thumb')!)
  expect(container.ownerDocument.querySelector('.lightbox')).not.toBeNull()
})

test('empty folder: the empty line', () => {
  render(FolderView, { props: { data: { session: 's', folder: 'downloads', uri: 'u', count: 0, files: [] } } })
  expect(screen.getByText('Nothing here yet.')).toBeInTheDocument()
})
