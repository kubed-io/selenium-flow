import { render, screen } from '@testing-library/svelte'
import { expect, test } from 'vitest'
import SiteView from './SiteView.svelte'

const ORIGIN = 'https://app.example.com'
const cookie = (name: string, value: string, extra = {}) =>
  ({ name, value, domain: 'app.example.com', path: '/', expiry: null, http_only: false, secure: false, same_site: null, shared: false, ...extra })
const detail = {
  site: 'app.example.com', uri: 'workspace://site-data/app.example.com',
  cookies: [
    cookie('sid', '•••', { http_only: true, secure: true }),
    cookie('pref', 'abc', { domain: '.example.com', shared: true, expiry: Date.now() / 1000 + 400 * 86400 }),
  ],
  storage: [{ origin: ORIGIN, local_storage: { theme: 'dark' }, session_storage: {} }],
  own_cookies: ['sid'], kept_shared: [{ name: 'pref', domain: '.example.com', path: '/' }],
}

test('cookies with their facts, httpOnly as the server masked it', () => {
  render(SiteView, { props: { data: detail } })
  expect(screen.getByText('app.example.com')).toBeInTheDocument()
  expect(screen.getByText('workspace://site-data/app.example.com')).toBeInTheDocument()
  expect(screen.getByText('•••')).toBeInTheDocument()
  expect(screen.getByText('httpOnly · secure · session')).toBeInTheDocument()
  expect(screen.getByText('.example.com · 1 y')).toBeInTheDocument()
})

test('storage per origin, only where it has entries', () => {
  render(SiteView, { props: { data: detail } })
  expect(screen.getByText(`Local storage · ${ORIGIN}`)).toBeInTheDocument()
  expect(screen.queryByText(/Session storage/)).toBeNull()
  expect(screen.getByText('theme')).toBeInTheDocument()
  expect(screen.getByText('dark')).toBeInTheDocument()
})

test('a host with nothing at all says so', () => {
  render(SiteView, { props: { data: { ...detail, cookies: [], storage: [] } } })
  expect(screen.getByText('Nothing saved for this site.')).toBeInTheDocument()
  expect(screen.queryByText('Cookies')).toBeNull()
})
