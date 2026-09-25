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
      { name: 'admin', description: 'The token.', keys: ['token'], restricted: true, allowed_urls: ['https://a'], source: 'file', location: '/s/admin',
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
  expect(admin).toHaveTextContent('fromfile · /s/admin')
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

test('off, and an error (S1)', async () => {
  fakeFetch({ 'GET /admin/secrets': { body: { enabled: false, secrets: [], undefined: [] } } })
  const r = render(SecretsPane, { api })
  await vi.waitFor(() => expect(screen.getByText('No secrets are configured on this server.')).toBeInTheDocument())
  r.unmount()
  fakeFetch({ 'GET /admin/secrets': { status: 500, body: { error: 'nope' } } })
  render(SecretsPane, { api })
  await vi.waitFor(() => expect(screen.getByText('nope')).toHaveClass('error'))
})
