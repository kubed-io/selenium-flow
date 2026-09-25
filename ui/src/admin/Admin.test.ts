import { fireEvent, render, screen } from '@testing-library/svelte'
import { beforeEach, expect, test, vi } from 'vitest'
import { FakeEventSource, fakeFetch } from '../test/helpers'
import Admin from './Admin.svelte'

const SESSIONS = { sessions: [{ key: 'k1', name: 'claudecode', live: true }], events_url: '/e' }
beforeEach(() => {
  sessionStorage.clear(); localStorage.clear()
  history.replaceState(null, '', '/#/')
  vi.stubGlobal('EventSource', FakeEventSource)
})

test('signed out: the sign-in card, and no Sign out (A1, A2)', async () => {
  fakeFetch({})
  const { container } = render(Admin, { mount: '', console: '/grid' })
  expect(container.querySelector('#login')).toBeVisible()
  expect(container.querySelector('#token')).toHaveAttribute('type', 'password')
  expect(container.querySelector('#token')).toBeRequired()
  expect(screen.queryByText('Sign out')).toBeNull()
})

test('a refused token says so; an accepted one is kept in sessionStorage only (A2)', async () => {
  let ok = false
  fakeFetch({ 'GET /admin/sessions': () => (ok ? { body: SESSIONS } : { status: 401 }) })
  const { container } = render(Admin, { mount: '', console: '/grid' })
  await fireEvent.input(container.querySelector('#token')!, { target: { value: ' bad ' } })
  await fireEvent.submit(container.querySelector('#loginForm')!)
  await vi.waitFor(() => expect(screen.getByText('That token was refused.')).toBeVisible())
  ok = true
  await fireEvent.input(container.querySelector('#token')!, { target: { value: ' good ' } })
  await fireEvent.submit(container.querySelector('#loginForm')!)
  await vi.waitFor(() => expect(screen.getByText('Sign out')).toBeInTheDocument())
  expect(sessionStorage.getItem('sf-token')).toBe('good')
  expect(localStorage.length).toBe(0)
  expect(screen.getByText('claudecode')).toBeInTheDocument()
})

test('a stored token is probed; a dead one lands on sign-in (A3)', async () => {
  sessionStorage.setItem('sf-token', 'old')
  fakeFetch({ 'GET /admin/sessions': { status: 401 } })
  const { container } = render(Admin, { mount: '', console: '/grid' })
  await vi.waitFor(() => expect(container.querySelector('#login')).toBeVisible())
  expect(sessionStorage.getItem('sf-token')).toBeNull()
})

test('BASE is the page path and server URLs resolve against ROOT (A6)', async () => {
  history.replaceState(null, '', '/base/flow/#/')
  sessionStorage.setItem('sf-token', 't')
  const { calls } = fakeFetch({ 'GET /base/flow/admin/sessions': { body: { ...SESSIONS, events_url: '/flow/admin/events?s=1' } } })
  render(Admin, { mount: '/flow', console: '/grid' })
  await vi.waitFor(() => expect(FakeEventSource.last?.url).toBe('/base/flow/admin/events?s=1'))
  expect(calls[0].path).toBe('/base/flow/admin/sessions')
})

test('three top tabs, one pane at a time; the live badge (R1, L1)', async () => {
  sessionStorage.setItem('sf-token', 't')
  fakeFetch({ 'GET /admin/sessions': { body: SESSIONS } })
  const { container } = render(Admin, { mount: '', console: '/grid' })
  await vi.waitFor(() => expect(screen.getByText('Live sessions')).toBeInTheDocument())
  expect(container.querySelector('#tabSessions')).toHaveAttribute('aria-selected', 'true')
  expect(container.querySelector('#live')).toHaveTextContent('connecting…')
  expect(container.querySelector('#paneSecrets')).toBeNull()
})

test('the console tab hides itself when it would frame this page (R3, C1)', async () => {
  sessionStorage.setItem('sf-token', 't')
  fakeFetch({ 'GET /admin/sessions': { body: SESSIONS } })
  const self = render(Admin, { mount: '', console: '/' })
  await vi.waitFor(() => expect(screen.getByText('Live sessions')).toBeInTheDocument())
  expect(self.container.querySelector('#tabConsole')).not.toBeVisible()
  self.unmount()
  history.replaceState(null, '', '/#/console')
  const other = render(Admin, { mount: '', console: '/grid' })
  await vi.waitFor(() => expect(other.container.querySelector('iframe.console')).toHaveAttribute('src', 'http://localhost:3000/grid'))
  expect(other.container.querySelector('#tabConsole')).toHaveAttribute('aria-selected', 'true')
})

test('picking a session routes through the hash (L3, R2)', async () => {
  sessionStorage.setItem('sf-token', 't')
  fakeFetch({ 'GET /admin/sessions': { body: SESSIONS } })
  render(Admin, { mount: '', console: '/grid' })
  await vi.waitFor(() => screen.getByText('claudecode'))
  await fireEvent.click(screen.getByText('claudecode'))
  expect(location.hash).toBe('#/sessions/k1')
})

test('Sign out stops the stream and forgets the token (A4)', async () => {
  sessionStorage.setItem('sf-token', 't')
  fakeFetch({ 'GET /admin/sessions': { body: SESSIONS } })
  const { container } = render(Admin, { mount: '', console: '/grid' })
  await vi.waitFor(() => screen.getByText('Sign out'))
  await fireEvent.click(screen.getByText('Sign out'))
  expect(FakeEventSource.last!.closed).toBe(true)
  expect(sessionStorage.getItem('sf-token')).toBeNull()
  expect(container.querySelector('#login')).toBeVisible()
})

// --- extra, from the traceability table ---------------------------------

test('the top tabs read Sessions, Secrets, Grid console left to right; #/secrets swaps the pane', async () => {
  sessionStorage.setItem('sf-token', 't')
  fakeFetch({ 'GET /admin/sessions': { body: SESSIONS } })
  const { container } = render(Admin, { mount: '', console: '/grid' })
  await vi.waitFor(() => expect(screen.getByText('Live sessions')).toBeInTheDocument())
  const tabs = [...container.querySelectorAll('.tabs button')]
  expect(tabs.map((b) => b.textContent?.trim())).toEqual(['Sessions', 'Secrets', 'Grid console'])

  await fireEvent.click(screen.getByText('Secrets'))
  await vi.waitFor(() => expect(container.querySelector('#paneSecrets')).toBeTruthy())
  expect(container.querySelector('#paneSecrets')?.closest('#app')).toBe(container.querySelector('#app'))
  expect(container.querySelector('#paneSessions')).toBeNull()
})

test('a deep link straight to a session (not the list) still opens the live stream (R5)', async () => {
  sessionStorage.setItem('sf-token', 't')
  history.replaceState(null, '', '/#/sessions/k1')
  fakeFetch({ 'GET /admin/sessions': { body: SESSIONS } })
  render(Admin, { mount: '', console: '/grid' })
  await vi.waitFor(() => expect(FakeEventSource.last).toBeTruthy())
  expect(FakeEventSource.last!.url).toBe('/e')
})

test('#/console with a self-framing console URL shows the session list, not the frame (ruling 3)', async () => {
  sessionStorage.setItem('sf-token', 't')
  history.replaceState(null, '', '/#/console')
  fakeFetch({ 'GET /admin/sessions': { body: SESSIONS } })
  const { container } = render(Admin, { mount: '', console: '/' })
  await vi.waitFor(() => expect(screen.getByText('Live sessions')).toBeInTheDocument())
  expect(container.querySelector('#paneSessions')).toBeInTheDocument()
  expect(container.querySelector('iframe.console')).toBeNull()
})
