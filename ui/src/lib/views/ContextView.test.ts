import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import ContextView from './ContextView.svelte'

const base = { workspace: 'mine', browser: 'firefox', url: 'https://example.com/a', live: true, window: '1280x800',
  principal: { kind: 'oidc', username: 'drk' }, site_data: { sites: 2 } }

test('a live workspace: name, mark, pill, link, window, principal, sites', () => {
  const { container } = render(ContextView, { props: { data: base } })
  expect(container.querySelector('strong')).toHaveTextContent('mine')
  expect(container.querySelector('.bmark')).toHaveTextContent('🦊')
  expect(screen.getByText('live')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'https://example.com/a' })).toHaveAttribute('rel', 'noopener noreferrer')
  expect(screen.getByText('1280x800')).toBeInTheDocument()
  expect(screen.getByText('drk')).toBeInTheDocument()
  expect(screen.getByText('2 sites')).toBeInTheDocument()
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
  expect(screen.getByTitle("Open isn't available in this client")).toHaveTextContent('Site data')
})
