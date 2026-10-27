import { render, screen } from '@testing-library/svelte'
import { expect, test } from 'vitest'
import WorkspaceSummary from './WorkspaceSummary.svelte'

test('the masthead: the name, how it is, and the last page; the facts are on Session', () => {
  const { container } = render(WorkspaceSummary, { data: { key: 'k', name: 'mine', owner: 'named', browser: 'firefox', session_id: 'id1', version: '130', live: false, url: 'https://a.b/' } })
  expect(container.querySelector('strong')).toHaveTextContent('mine')
  expect(screen.getByText('idle')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'https://a.b/' })).toHaveAttribute('rel', 'noopener noreferrer')
  expect(container.querySelector('.groups')).toBeNull()
  expect(screen.queryByText('id1')).toBeNull()
})

test('unnamed shows who holds it; a javascript: page is text; nowhere yet', () => {
  const r = render(WorkspaceSummary, { data: { key: 'stdio', owner: 'stdio', url: 'javascript:x' } })
  expect(r.container.querySelector('strong')).toHaveTextContent('stdio')
  expect(screen.queryByRole('link')).toBeNull()
  expect(screen.getByText('javascript:x')).toBeInTheDocument()
  r.unmount()
  render(WorkspaceSummary, { data: { key: 'x' } })
  expect(screen.getByText('nowhere yet')).toBeInTheDocument()
  expect(screen.getByText('Workspace')).toBeInTheDocument()
})

test('a recording live workspace wears ● REC; otherwise none (R7)', () => {
  const r = render(WorkspaceSummary, { data: { key: 'k', live: true, recording: true } })
  expect(screen.getByText('● REC')).toHaveClass('pill', 'rec')
  r.unmount()
  render(WorkspaceSummary, { data: { key: 'k', live: true, recording: false } })
  expect(screen.queryByText('● REC')).toBeNull()
})

test('the masthead wears ● capture or capture paused, as it did', () => {
  const r = render(WorkspaceSummary, { data: { key: 'k', live: true, capture: true, capturing: true } })
  expect(screen.getByText('● capture')).toHaveClass('pill', 'capture')
  r.unmount()
  render(WorkspaceSummary, { data: { key: 'k', live: false, capture: true, capturing: false } })
  expect(screen.getByText('capture paused')).toHaveClass('pill', 'capture-paused')
})
