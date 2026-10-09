import { render, screen } from '@testing-library/svelte'
import { expect, test } from 'vitest'
import WorkspaceSummary from './WorkspaceSummary.svelte'

test('named: no key or held-by; groups by lifetime (D1)', () => {
  const { container } = render(WorkspaceSummary, { data: { key: 'k', name: 'mine', owner: 'named', browser: 'firefox', session_id: 'id1', version: '130', live: false, url: 'https://a.b/' } })
  expect(container.querySelector('strong')).toHaveTextContent('mine')
  expect(screen.getByText('idle')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'https://a.b/' })).toHaveAttribute('rel', 'noopener noreferrer')
  const labels = [...container.querySelectorAll('.group > .label')].map((e) => e.textContent)
  expect(labels).toEqual(['workspace', 'browser'])
  expect(screen.queryByText('key')).toBeNull()
})

test('unnamed shows key and held-by; a javascript: page is text; nowhere yet', () => {
  const r = render(WorkspaceSummary, { data: { key: 'stdio', owner: 'stdio', url: 'javascript:x' } })
  expect(screen.getByText('key')).toBeInTheDocument()
  expect(screen.getByText('held by')).toBeInTheDocument()
  expect(screen.queryByRole('link')).toBeNull()
  expect(screen.getByText('javascript:x')).toBeInTheDocument()
  r.unmount()
  render(WorkspaceSummary, { data: { key: 'x' } })
  expect(screen.getByText('nowhere yet')).toBeInTheDocument()
  expect(screen.getByText('x')).toBeInTheDocument()
})

test('a recording live workspace wears ● REC; otherwise none (R7)', () => {
  const r = render(WorkspaceSummary, { data: { key: 'k', live: true, recording: true } })
  expect(screen.getByText('● REC')).toHaveClass('pill', 'rec')
  r.unmount()
  render(WorkspaceSummary, { data: { key: 'k', live: true, recording: false } })
  expect(screen.queryByText('● REC')).toBeNull()
})

test('the idle timeout sits with the session, live and idle, hidden when unknown (spec ruling 11)', () => {
  const live = render(WorkspaceSummary, { data: { key: 'k', live: true, session_id: 'id1', grid_timeout: 300 } })
  const fact = screen.getByText('idle timeout').closest('.group')
  expect(fact?.querySelector('.label')).toHaveTextContent('browser')
  expect(screen.getByText('300 s · read from the Grid node')).toBeInTheDocument()
  live.unmount()
  const idle = render(WorkspaceSummary, { data: { key: 'k', live: false, grid_timeout: 300 } })
  expect(screen.getByText('300 s · no session is open')).toBeInTheDocument()
  idle.unmount()
  render(WorkspaceSummary, { data: { key: 'k', live: true, grid_timeout: null } })
  expect(screen.queryByText('idle timeout')).toBeNull()
})
