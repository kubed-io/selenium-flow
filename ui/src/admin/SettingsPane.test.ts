import { render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import { fakeFetch } from '../test/helpers'
import { createApi } from './api'
import SettingsPane from './SettingsPane.svelte'

const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })

const body = {
  sections: [
    { name: 'server', description: 'Where it listens, and where it lives.', settings: [
      { key: 'port', name: 'port', description: 'Port to listen on.', value: 8000, source: 'args' },
      { key: 'public_base_url', name: 'public_base_url', description: 'Where browsers reach this server, for links.', value: null, source: 'default' },
    ] },
    { name: 'auth', description: 'The bearer token every request needs.', settings: [
      { key: 'auth.token', name: 'token', description: 'Bearer token for every request. Unset is open.', value: null, source: 'env', sensitive: true, set: true },
    ] },
    { name: 'redis', description: 'The workspace store’s connection.', settings: [
      { key: 'redis.db', name: 'db', description: 'Redis database number.', value: 2, source: 'config' },
      { key: 'redis.password', name: 'password', description: 'Redis password.', value: null, source: 'default', sensitive: true, set: false },
    ] },
    { name: 'secrets', description: 'Where secrets are read from.', settings: [
      { key: 'secrets.dirs', name: 'dirs', description: 'Directories of secrets. First match wins.', value: ['/a', '/b'], source: 'config' },
    ] },
  ],
}

const row = (c: HTMLElement, key: string) => c.querySelector(`.setting[data-key="${key}"]`) as HTMLElement

test('a card per section, a row per setting, a pill per source', async () => {
  fakeFetch({ 'GET /admin/settings': { body } })
  const { container } = render(SettingsPane, { api })
  expect(screen.getByText('Loading…')).toBeInTheDocument()
  await vi.waitFor(() => expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument())
  expect(container.querySelectorAll('.settings-card')).toHaveLength(4)
  expect(row(container, 'port').querySelector('.value')).toHaveTextContent('8000')
  expect(row(container, 'port').querySelector('.pill.src')).toHaveTextContent('args')
  expect(row(container, 'public_base_url').querySelector('.value')).toHaveTextContent('—')
  expect(row(container, 'secrets.dirs').querySelector('.value')).toHaveTextContent('/a, /b')
})

test('a config-sourced row shows the pill and nothing else about the file', async () => {
  fakeFetch({ 'GET /admin/settings': { body } })
  const { container } = render(SettingsPane, { api })
  await vi.waitFor(() => expect(container.querySelector('.settings-card')).toBeTruthy())
  expect(row(container, 'redis.db').querySelector('.file')).toBeNull()
  expect(row(container, 'redis.db')).not.toHaveTextContent(/config\.yaml|\/etc\//)
  expect(row(container, 'redis.db').querySelector('.pill.src')).toHaveTextContent('config')
})

test('a sensitive value is dots when set and nothing when not, and never labelled', async () => {
  fakeFetch({ 'GET /admin/settings': { body } })
  const { container } = render(SettingsPane, { api })
  await vi.waitFor(() => expect(container.querySelector('.settings-card')).toBeTruthy())
  expect(row(container, 'auth.token').querySelector('.value')?.textContent).toBe('●●●●')
  expect(row(container, 'redis.password').querySelector('.value')?.textContent).toBe('')
  expect(container).not.toHaveTextContent(/sensitive|not set/i)
})

test('the ⓘ carries the description, the legend is four bare pills, and the wiki is linked', async () => {
  fakeFetch({ 'GET /admin/settings': { body } })
  const { container } = render(SettingsPane, { api })
  await vi.waitFor(() => expect(container.querySelector('.settings-card')).toBeTruthy())
  expect(row(container, 'port').querySelector('.info')).toHaveAttribute('data-tip', 'Port to listen on.')
  expect(row(container, 'port').querySelector('.info')).toHaveAttribute('tabindex', '0')
  const legend = container.querySelector('.legend') as HTMLElement
  expect(Array.from(legend.querySelectorAll('.pill')).map((p) => p.textContent)).toEqual(['default', 'config', 'env', 'args'])
  expect(legend.textContent?.replace(/default|config|env|args|\s/g, '')).toBe('')
  expect(screen.getByRole('link', { name: /wiki/ })).toHaveAttribute('href', 'https://github.com/kubed-io/selenium-flow/wiki/Configuration')
})

test('a key shows without its section: the card title is the section', async () => {
  fakeFetch({ 'GET /admin/settings': { body } })
  const { container } = render(SettingsPane, { api })
  await vi.waitFor(() => expect(container.querySelector('.settings-card')).toBeTruthy())
  expect(row(container, 'redis.db').querySelector('.key')?.textContent).toBe('db')
  expect(row(container, 'port').querySelector('.key')?.textContent).toBe('port')
})

test('nothing on the tab expands', async () => {
  fakeFetch({ 'GET /admin/settings': { body } })
  const { container } = render(SettingsPane, { api })
  await vi.waitFor(() => expect(container.querySelector('.settings-card')).toBeTruthy())
  expect(container.querySelectorAll('button, details, [aria-expanded]')).toHaveLength(0)
})

test('an error', async () => {
  fakeFetch({ 'GET /admin/settings': { status: 500, body: { error: 'nope' } } })
  render(SettingsPane, { api })
  await vi.waitFor(() => expect(screen.getByText('nope')).toHaveClass('error'))
})
