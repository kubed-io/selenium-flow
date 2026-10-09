import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import DocumentView from './DocumentView.svelte'

const URI = 'skill://selenium-flow/references/TROUBLESHOOTING.md'
const page = (n: number) =>
  '# When something goes wrong\n\n' + Array.from({ length: n }, (_, i) => `Paragraph ${i + 1}.`).join('\n\n') + '\n'

test('markdown: the # heading is the title beside the URI', () => {
  render(DocumentView, { props: { data: page(2), uri: URI } })
  expect(screen.getByText('When something goes wrong').closest('strong')).not.toBeNull()
  expect(screen.getByText(URI)).toBeInTheDocument()
  expect(screen.getByText('Paragraph 2.')).toBeInTheDocument()
  expect(screen.queryByRole('heading', { level: 1 })).toBeNull()
})

test('inline with fullscreen on offer: six blocks and +N more', () => {
  render(DocumentView, { props: { data: page(9), uri: URI, expandable: true } })
  expect(screen.getByText('Paragraph 6.')).toBeInTheDocument()
  expect(screen.queryByText('Paragraph 7.')).toBeNull()
  expect(screen.getByText('+3 more')).toBeInTheDocument()
})

test('fullscreen, or no fullscreen to offer: everything', () => {
  render(DocumentView, { props: { data: page(9), uri: URI, expandable: true, expanded: true } })
  expect(screen.getByText('Paragraph 9.')).toBeInTheDocument()
  expect(screen.queryByText(/more$/)).toBeNull()
})

test('without fullscreen the whole document draws', () => {
  render(DocumentView, { props: { data: page(9), uri: URI, expandable: false } })
  expect(screen.getByText('Paragraph 9.')).toBeInTheDocument()
})

test('no # heading: the file name is the title', () => {
  render(DocumentView, { props: { data: 'Just text.', uri: 'skill://selenium-flow/references/NOTES.md' } })
  expect(screen.getByText('NOTES.md')).toBeInTheDocument()
})

test('a URI in the text drills through show', async () => {
  const onshow = vi.fn()
  render(DocumentView, { props: { data: '# T\n\nRead `skill://selenium-flow/references/FLOWS.md`.\n', uri: URI, onshow } })
  await fireEvent.click(screen.getByRole('button', { name: 'skill://selenium-flow/references/FLOWS.md' }))
  expect(onshow).toHaveBeenCalledWith('skill://selenium-flow/references/FLOWS.md')
})

test('JSON is a tree, titled by its title or its URI', () => {
  const { container, unmount } = render(DocumentView, { props: { data: { title: 'Flow', type: 'object' }, uri: 'flow://schema' } })
  expect(screen.getByText('Flow', { selector: 'strong' })).toBeInTheDocument()
  expect(container.querySelector('details')).not.toBeNull()
  unmount()
  render(DocumentView, { props: { data: { skill: 'selenium-flow', files: [] }, uri: 'skill://selenium-flow/_manifest' } })
  expect(screen.getByText('_manifest', { selector: 'strong' })).toBeInTheDocument()
})
