import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import FilesView from './FilesView.svelte'

const data = {
  session: 's', count: 1, files: [{ name: 'k.pdf', size: 5, url: 'https://example.com/k' }],
  folders: [{ name: 'screenshots', uri: 'session://files/screenshots', count: 4 },
    { name: 'recordings', uri: 'session://files/recordings', count: 2 },
    { name: 'downloads', uri: 'session://files/downloads', count: 1, browser: true }],
}

test('kept files in one horizontal row and a chip per folder; a chip drills in', async () => {
  const onshow = vi.fn()
  const { container } = render(FilesView, { props: { data, onshow } })
  expect(container.querySelectorAll('.files.strip > .file')).toHaveLength(1)
  expect(container.querySelector('section')).not.toHaveClass('row')
  const chip = screen.getByRole('button', { name: /Screenshots/ })
  expect(chip).toHaveTextContent('4')
  expect(screen.getByRole('button', { name: /Downloads/ })).toHaveTextContent('1')
  expect(screen.getByRole('button', { name: /Recordings/ })).toHaveTextContent('2')
  await fireEvent.click(chip)
  expect(onshow).toHaveBeenCalledWith('session://files/screenshots')
})

test('without onshow the chips are not buttons', () => {
  render(FilesView, { props: { data } })
  expect(screen.queryByRole('button')).toBeNull()
  expect(screen.getByText('Screenshots')).toBeInTheDocument()
  expect(screen.getAllByTitle("Open isn't available in this client")).toHaveLength(3)
})

test('a chip that opens carries no unavailable tooltip', () => {
  render(FilesView, { props: { data, onshow: () => {} } })
  expect(screen.queryByTitle("Open isn't available in this client")).toBeNull()
})
