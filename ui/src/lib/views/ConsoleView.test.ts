import { render, screen } from '@testing-library/svelte'
import { expect, test } from 'vitest'
import ConsoleView from './ConsoleView.svelte'
import type { ConsoleEntry } from '../types'

const at = new Date(2026, 9, 10, 12, 1, 43).getTime() / 1000
const e = (seq: number, level: string, text: string, type = 'console'): ConsoleEntry =>
  ({ seq, at: at + seq, level, type, text, source: null, context: '', nav: 1 })
const data = {
  capture: true as const, pages: [], count: 5, total: 5, not_kept: { debug: 0 },
  entries: [e(1, 'info', 'app booted'), e(2, 'warn', 'deprecated'), e(3, 'error', 'POST failed'), e(4, 'error', 'Uncaught TypeError', 'javascript'), e(5, 'log', 'last <b>bold</b>')],
}

test('newest first, at most four rows, counts and the rest', () => {
  render(ConsoleView, { props: { data, uri: 'workspace://console' } })
  expect(screen.getByText('Console')).toBeInTheDocument()
  expect(screen.getByText('workspace://console')).toBeInTheDocument()
  const rows = screen.getAllByRole('listitem')
  expect(rows).toHaveLength(4)
  expect(rows[0]).toHaveTextContent('last <b>bold</b>')
  expect(rows[0]).toHaveTextContent('12:01:48')
  expect(screen.getByText('Errors').nextElementSibling).toHaveTextContent('2')
  expect(screen.getByText('Warnings').nextElementSibling).toHaveTextContent('1')
  expect(screen.getByText('+1 more · since the last navigation')).toBeInTheDocument()
})

test('captured text is text, never markup', () => {
  const { container } = render(ConsoleView, { props: { data } })
  expect(container.querySelector('b')).toBeNull()
})

test('capture off says how to turn it on', () => {
  render(ConsoleView, { props: { data: { capture: false, how: 'open_session(capture=true)' } } })
  expect(screen.getByText('Capture is off for this workspace. Open the browser with open_session(capture=true).')).toBeInTheDocument()
})
