import { render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import { fakeFetch } from '../test/helpers'
import { createApi } from './api'
import SecretsPane from './SecretsPane.svelte'

const api = createApi({ base: '', token: () => 't', onUnauthorized: () => {} })

test('Loading…, then a card per secret with keys, allowed, source and backlinks (S1)', async () => {
  fakeFetch({ 'GET /admin/secrets': { body: {
    enabled: true,
    secrets: [
      { name: 'admin', description: 'The token.', keys: ['token'], restricted: true, allowed_urls: ['https://a'],
        origins: [{ source: 'filesystem', location: '/s/admin' }],
        uses: [{ flow: 'login', steps: [2], session: 'k 1' }, { flow: 'g', steps: [1, 3], shared: true }] },
      { name: 'open', keys: ['k'], restricted: false, uses: [] },
      { name: 'bad', keys: ['k'], allowed_urls_rejected: ['ftp://x'], uses: [] },
    ],
    undefined: [{ name: 'ghost', uses: [{ flow: 'f', steps: [1], session: 's' }] }],
  } } })
  const { container } = render(SecretsPane, { api })
  expect(screen.getByText('Loading…')).toBeInTheDocument()
  await vi.waitFor(() => expect(screen.getByRole('heading', { name: 'Secrets' })).toBeInTheDocument())
  const [admin, open, bad, ghost] = container.querySelectorAll('.card.secret')
  expect(admin).toHaveTextContent('🔑admin')
  expect(admin).toHaveTextContent('allowedhttps://a')
  expect(admin).toHaveTextContent('fromfilesystem · /s/admin')
  expect(admin.querySelector('a')).toHaveAttribute('href', '#/sessions/k%201/flows/login')
  expect(admin.querySelector('a')).toHaveTextContent('k 1 →')
  expect(admin).toHaveTextContent('steps 1, 3')
  expect(admin).toHaveTextContent('🌐 shared')
  expect(open.querySelector('.pill.warn')).toHaveTextContent('any site')
  expect(open).toHaveTextContent('No flow uses it.')
  expect(bad.querySelector('.pill.warn')).toHaveTextContent('unusable until fixed')
  expect(bad).toHaveTextContent('allowed_urls: ftp://x')
  expect(screen.getByText('Named by a flow, not defined')).toBeInTheDocument()
  expect(ghost.querySelector('.pill.warn')).toHaveTextContent('not defined')
})

test('uses keyed by session+flow do not collide when the concatenation does', async () => {
  fakeFetch({ 'GET /admin/secrets': { body: {
    enabled: true,
    secrets: [
      { name: 's', keys: ['k'], uses: [{ flow: 'ab', steps: [1], session: 'c' }, { flow: 'a', steps: [1], session: 'bc' }] },
    ],
    undefined: [],
  } } })
  const { container } = render(SecretsPane, { api })
  await vi.waitFor(() => expect(screen.getByRole('heading', { name: 'Secrets' })).toBeInTheDocument())
  const rows = container.querySelectorAll('.use')
  expect(rows).toHaveLength(2)
  const hrefs = Array.from(rows).map((r) => r.querySelector('a')?.getAttribute('href'))
  expect(hrefs).toContain('#/sessions/c/flows/ab')
  expect(hrefs).toContain('#/sessions/bc/flows/a')
})

test('config secrets: key sources, every origin, and the two new warnings', async () => {
  fakeFetch({ 'GET /admin/secrets': { body: {
    enabled: true,
    secrets: [
      { name: 'grafana', keys: ['password'], restricted: true, allowed_urls: ['https://g'],
        origins: [{ source: 'filesystem', location: '/secrets' }, { source: 'config', location: '/etc/c.yaml' }],
        key_sources: { password: { from: 'filesystem' } }, uses: [] },
      { name: 'admin', keys: ['token'], restricted: true, allowed_urls: ['https://s'],
        origins: [{ source: 'config', location: '/etc/c.yaml' }], key_sources: { token: { from: 'env', name: 'AUTH_TOKEN' } }, uses: [] },
      { name: 'demo', keys: ['password'], restricted: true, allowed_urls: ['http://l'], inline_keys: ['password'],
        origins: [{ source: 'config', location: '/etc/c.yaml' }], key_sources: { password: { from: 'value' } }, uses: [] },
      { name: 'github', keys: ['token'], restricted: true, allowed_urls: ['https://github.com'],
        keys_unresolved: [{ key: 'token', reason: 'env GITHUB_TOKEN is not set' }],
        origins: [{ source: 'config', location: '/etc/c.yaml' }], key_sources: { token: { from: 'env', name: 'GITHUB_TOKEN' } }, uses: [] },
    ],
    undefined: [],
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
        key_sources: { k: { from: 'value' } }, uses: [] },
    ],
    undefined: [],
  } } })
  const { container } = render(SecretsPane, { api })
  await vi.waitFor(() => expect(screen.getByRole('heading', { name: 'Secrets' })).toBeInTheDocument())
  const [card] = container.querySelectorAll('.card.secret')
  const pills = Array.from(card.querySelectorAll('.pill.warn')).map((p) => p.textContent)
  expect(pills).toContain('inline value')
  expect(pills).toContain('any site')
})

test('off, and an error (S1)', async () => {
  fakeFetch({ 'GET /admin/secrets': { body: { enabled: false, secrets: [], undefined: [] } } })
  const r = render(SecretsPane, { api })
  await vi.waitFor(() => expect(screen.getByText('No secrets are configured on this server.')).toBeInTheDocument())
  r.unmount()
  fakeFetch({ 'GET /admin/secrets': { status: 500, body: { error: 'nope' } } })
  render(SecretsPane, { api })
  await vi.waitFor(() => expect(screen.getByText('nope')).toHaveClass('error'))
})
