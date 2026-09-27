import { render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import { fakeFetch } from '../test/helpers'
import { createApi } from './api'
import SecretsPane from './SecretsPane.svelte'

const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })

test('Loading…, then a card per secret with keys, allowed and source (S1)', async () => {
  fakeFetch({ 'GET /admin/secrets': { body: {
    enabled: true,
    secrets: [
      { name: 'admin', description: 'The token.', keys: ['token'], restricted: true, allowed_urls: ['https://a'],
        origins: [{ source: 'filesystem', location: '/s/admin' }] },
      { name: 'open', keys: ['k'], restricted: false },
      { name: 'bad', keys: ['k'], allowed_urls_rejected: ['ftp://x'] },
    ],
  } } })
  const { container } = render(SecretsPane, { api })
  expect(screen.getByText('Loading…')).toBeInTheDocument()
  await vi.waitFor(() => expect(screen.getByRole('heading', { name: 'Secrets' })).toBeInTheDocument())
  const [admin, open, bad] = container.querySelectorAll('.card.secret')
  expect(admin).toHaveTextContent('🔑admin')
  expect(admin).toHaveTextContent('allowedhttps://a')
  expect(admin).toHaveTextContent('fromfilesystem · /s/admin')
  expect(open.querySelector('.pill.warn')).toHaveTextContent('any site')
  expect(bad.querySelector('.pill.warn')).toHaveTextContent('unusable until fixed')
  expect(bad).toHaveTextContent('allowed_urls: ftp://x')
})

test('config secrets: key sources, every origin, and the two new warnings', async () => {
  fakeFetch({ 'GET /admin/secrets': { body: {
    enabled: true,
    secrets: [
      { name: 'grafana', keys: ['password'], restricted: true, allowed_urls: ['https://g'],
        origins: [{ source: 'filesystem', location: '/secrets' }, { source: 'config', location: '/etc/c.yaml' }],
        key_sources: { password: { from: 'filesystem' } } },
      { name: 'admin', keys: ['token'], restricted: true, allowed_urls: ['https://s'],
        origins: [{ source: 'config', location: '/etc/c.yaml' }], key_sources: { token: { from: 'env', name: 'AUTH_TOKEN' } } },
      { name: 'demo', keys: ['password'], restricted: true, allowed_urls: ['http://l'], inline_keys: ['password'],
        origins: [{ source: 'config', location: '/etc/c.yaml' }], key_sources: { password: { from: 'value' } } },
      { name: 'github', keys: ['token'], restricted: true, allowed_urls: ['https://github.com'],
        keys_unresolved: [{ key: 'token', reason: 'env GITHUB_TOKEN is not set' }],
        origins: [{ source: 'config', location: '/etc/c.yaml' }], key_sources: { token: { from: 'env', name: 'GITHUB_TOKEN' } } },
    ],
  } } })
  const { container } = render(SecretsPane, { api })
  await vi.waitFor(() => expect(screen.getByRole('heading', { name: 'Secrets' })).toBeInTheDocument())
  const [grafana, admin, demo, github] = container.querySelectorAll('.card.secret')
  expect(grafana).toHaveTextContent('fromfilesystem · /secrets + config · /etc/c.yaml')
  expect(grafana).toHaveTextContent('keyspassword')  // a directory key stays bare
  expect(admin).toHaveTextContent('token · env AUTH_TOKEN')
  expect(demo.querySelector('.pill.warn')).toHaveTextContent('inline value')
  expect(demo).toHaveTextContent('password · value')
  expect(github.querySelector('.pill.warn')).toHaveTextContent('key unresolved')
  expect(github).toHaveTextContent('token: env GITHUB_TOKEN is not set')
})

test('an unrestricted secret with an inline key shows both its warnings, not just one', async () => {
  fakeFetch({ 'GET /admin/secrets': { body: {
    enabled: true,
    secrets: [
      { name: 'wide-open', keys: ['k'], restricted: false, inline_keys: ['k'],
        key_sources: { k: { from: 'value' } } },
    ],
  } } })
  const { container } = render(SecretsPane, { api })
  await vi.waitFor(() => expect(screen.getByRole('heading', { name: 'Secrets' })).toBeInTheDocument())
  const [card] = container.querySelectorAll('.card.secret')
  const pills = Array.from(card.querySelectorAll('.pill.warn')).map((p) => p.textContent)
  expect(pills).toContain('inline value')
  expect(pills).toContain('any site')
})

test('a broken leash and unresolved keys both show, not just the leash', async () => {
  fakeFetch({ 'GET /admin/secrets': { body: {
    enabled: true,
    secrets: [
      { name: 'both', keys: ['token'], restricted: true, allowed_urls_rejected: ['ftp://x'],
        keys_unresolved: [{ key: 'token', reason: 'env GITHUB_TOKEN is not set' }] },
    ],
  } } })
  const { container } = render(SecretsPane, { api })
  await vi.waitFor(() => expect(screen.getByRole('heading', { name: 'Secrets' })).toBeInTheDocument())
  const [both] = container.querySelectorAll('.card.secret')
  expect(both).toHaveTextContent('allowed_urls: ftp://x')
  expect(both).toHaveTextContent('token: env GITHUB_TOKEN is not set')
})

test('off, and an error (S1)', async () => {
  fakeFetch({ 'GET /admin/secrets': { body: { enabled: false, secrets: [] } } })
  const r = render(SecretsPane, { api })
  await vi.waitFor(() => expect(screen.getByText('No secrets are configured on this server.')).toBeInTheDocument())
  r.unmount()
  fakeFetch({ 'GET /admin/secrets': { status: 500, body: { error: 'nope' } } })
  render(SecretsPane, { api })
  await vi.waitFor(() => expect(screen.getByText('nope')).toHaveClass('error'))
})
