import { render, screen } from '@testing-library/svelte'
import { expect, test } from 'vitest'
import SessionSummary from './SessionSummary.svelte'

test('named: no key or held-by; groups by lifetime (D1)', () => {
  const { container } = render(SessionSummary, { data: { key: 'k', name: 'mine', owner: 'named', browser: 'firefox', session_id: 'id1', version: '130', live: false, url: 'https://a.b/' } })
  expect(container.querySelector('strong')).toHaveTextContent('mine')
  expect(screen.getByText('idle')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'https://a.b/' })).toHaveAttribute('rel', 'noopener noreferrer')
  const labels = [...container.querySelectorAll('.group > .label')].map((e) => e.textContent)
  expect(labels).toEqual(['session', 'browser'])
  expect(screen.queryByText('key')).toBeNull()
})

test('unnamed shows key and held-by; a javascript: page is text; nowhere yet', () => {
  const r = render(SessionSummary, { data: { key: 'stdio', owner: 'stdio', url: 'javascript:x' } })
  expect(screen.getByText('key')).toBeInTheDocument()
  expect(screen.getByText('held by')).toBeInTheDocument()
  expect(screen.queryByRole('link')).toBeNull()
  expect(screen.getByText('javascript:x')).toBeInTheDocument()
  r.unmount()
  render(SessionSummary, { data: { key: 'x' } })
  expect(screen.getByText('nowhere yet')).toBeInTheDocument()
  expect(screen.getByText('x')).toBeInTheDocument()
})
