import { render, screen } from '@testing-library/svelte'
import { expect, test } from 'vitest'
import WorkspaceSummary from './WorkspaceSummary.svelte'

test('the card carries the workspace only: its name, whether it is kept, its config (UI-64)', () => {
  const { container } = render(WorkspaceSummary, { data: { key: 'k', name: 'mine', kept: true, live: true, recording: true, capture: true, capturing: true, url: 'https://a.b/' } })
  expect(container.querySelector('strong')).toHaveTextContent('mine')
  expect(screen.getByText('kept · config')).toBeInTheDocument()
})

test('the workspace card carries no session state', () => {
  const { container } = render(WorkspaceSummary, { data: { key: 'k', name: 'mine', owner: 'named', browser: 'firefox', session_id: 'id1', version: '130', live: true, recording: true, capture: true, capturing: true, url: 'https://a.b/' } })
  for (const text of ['open', 'idle', '● REC', '● capture', 'capture paused', 'last page', 'https://a.b/', 'id1']) expect(screen.queryByText(text)).toBeNull()
  expect(screen.queryByRole('link')).toBeNull()
  expect(container.querySelector('.pill')).toBeNull()
})

test('unnamed shows who holds it', () => {
  const r = render(WorkspaceSummary, { data: { key: 'stdio', owner: 'stdio' } })
  expect(r.container.querySelector('strong')).toHaveTextContent('stdio')
  r.unmount()
  render(WorkspaceSummary, { data: { key: 'x' } })
  expect(screen.getByText('Workspace')).toBeInTheDocument()
  expect(screen.queryByText('kept · config')).toBeNull()
})
