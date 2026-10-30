import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import ContextView from './ContextView.svelte'

const base = { workspace: 'mine', browser: 'firefox', url: 'https://example.com/a', live: true, window: '1400x1000',
  principal: { kind: 'oidc', username: 'drk' }, site_data: { sites: 2 } }

test('a live workspace: name, mark, pill, link, window, principal, sites', () => {
  const { container } = render(ContextView, { props: { data: base } })
  expect(container.querySelector('strong')).toHaveTextContent('mine')
  expect(container.querySelector('.bmark')).toHaveTextContent('🦊')
  expect(screen.getByText('open')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'https://example.com/a' })).toHaveAttribute('rel', 'noopener noreferrer')
  expect(screen.getByText('1400×1000')).toBeInTheDocument()
  expect(screen.getByText('drk')).toBeInTheDocument()
})

test('no principal, no url, idle', () => {
  render(ContextView, { props: { data: { workspace: 's', live: false, principal: null, url: null } } })
  expect(screen.getByText('idle')).toBeInTheDocument()
  expect(screen.getByText('nowhere yet')).toBeInTheDocument()
  expect(screen.queryByText('drk')).toBeNull()
  expect(screen.queryByText(/site/)).toBeNull()
})

test('a principal without a username shows its kind; javascript: is text', () => {
  render(ContextView, { props: { data: { ...base, url: 'javascript:x', principal: { kind: 'admin' } } } })
  expect(screen.getByText('admin')).toBeInTheDocument()
  expect(screen.queryByRole('link')).toBeNull()
})

test('the Site data chip drills into the saved site data', async () => {
  const onshow = vi.fn()
  render(ContextView, { props: { data: { ...base, site_data: { sites: 2, uri: 'workspace://site-data' } }, onshow } })
  const chip = screen.getByRole('button', { name: /Site data/ })
  expect(chip).toHaveTextContent('2')
  await fireEvent.click(chip)
  expect(onshow).toHaveBeenCalledWith('workspace://site-data')
})

test('without onshow the chip is not a button', () => {
  render(ContextView, { props: { data: { ...base, site_data: { sites: 2, uri: 'workspace://site-data' } } } })
  expect(screen.queryByRole('button')).toBeNull()
  expect(screen.getAllByTitle("Open isn't available in this client").map((e) => e.textContent)).toEqual(
    [expect.stringContaining('Site data'), expect.stringContaining('Downloads')])
})

test('capture paused and the two chips', async () => {
  const onshow = vi.fn()
  render(ContextView, { props: { data: { ...base, live: false, capture: true, capturing: false,
    captured: { console: 4, network: 9, errors: 2, failed_requests: 1 } }, onshow } })
  expect(screen.getByText('capture paused')).toHaveClass('pill', 'capture-paused')
  expect(screen.getByText('2 errors')).toBeInTheDocument()
  expect(screen.getByText('1 failed')).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: /Console/ }))
  await fireEvent.click(screen.getByRole('button', { name: /Network/ }))
  // With errors, Console opens on them: a chatty page's whole console is
  // more than show draws, and the errors are why anyone clicks (spotless 8).
  expect(onshow.mock.calls).toEqual([['workspace://console?level=error'], ['workspace://network']])
})

test('without errors the Console chip opens the whole console', async () => {
  const onshow = vi.fn()
  render(ContextView, { props: { data: { ...base, capture: true, capturing: true,
    captured: { console: 4, network: 9, errors: 0, failed_requests: 0 } }, onshow } })
  await fireEvent.click(screen.getByRole('button', { name: /Console/ }))
  expect(onshow).toHaveBeenCalledWith('workspace://console')
})

test('REC and capturing pills; chips are inert without onshow', () => {
  render(ContextView, { props: { data: { ...base, settings: { record: true }, capture: true, capturing: true,
    captured: { console: 0, network: 0, errors: 0, failed_requests: 0 } } } })
  expect(screen.getByText('● REC')).toHaveClass('pill', 'rec')
  expect(screen.getByText('● capture')).toHaveClass('pill', 'capture')
  expect(screen.queryByRole('button')).toBeNull()
  expect(screen.getAllByTitle("Open isn't available in this client").length).toBeGreaterThanOrEqual(2)
})

test('an open session offers its downloads', async () => {
  const onshow = vi.fn()
  const { getByRole } = render(ContextView, { props: { data: { ...base, live: true }, onshow } })
  await fireEvent.click(getByRole('button', { name: 'Downloads' }))
  expect(onshow).toHaveBeenCalledWith('session://downloads')
})

test('an idle workspace offers no downloads', () => {
  const { queryByText } = render(ContextView, { props: { data: { ...base, live: false }, onshow: vi.fn() } })
  expect(queryByText('Downloads')).toBeNull()
})

test('without onshow the Downloads chip is the disabled span', () => {
  render(ContextView, { props: { data: { ...base, live: true, site_data: undefined } } })
  expect(screen.queryByRole('button')).toBeNull()
  expect(screen.getByTitle("Open isn't available in this client")).toHaveTextContent('Downloads')
})

test('no capture pills or chips when capture is off; REC needs a live browser', () => {
  render(ContextView, { props: { data: { ...base, live: false, settings: { record: true }, site_data: undefined } } })
  expect(screen.queryByText('● REC')).toBeNull()
  expect(screen.queryByText(/capture/)).toBeNull()
  expect(screen.queryByText('Console')).toBeNull()
})
