import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test } from 'vitest'
import SecretsView from './SecretsView.svelte'

const data = {
  workspace: 's', count: 2,
  secrets: [
    {
      name: 'admin', description: 'The admin login', keys: ['username', 'password'],
      restricted: true, allowed_urls: ['https://example.test/'],
      key_sources: { password: { from: 'env' as const, name: 'ADMIN_PW' } },
      origins: [{ source: 'config' as const, location: '/etc/c.yaml' }],
    },
    { name: 'open', keys: ['token'], restricted: false },
  ],
}

test('one card per secret in a list scroller', () => {
  render(SecretsView, { props: { data } })
  expect(screen.getByRole('list')).toBeInTheDocument()
  expect(screen.getAllByRole('listitem')).toHaveLength(2)
  expect(screen.getByText('The admin login')).toBeInTheDocument()
  expect(screen.getByText('2 keys · 1 site')).toBeInTheDocument()
  expect(screen.getByText('1 key · any site')).toBeInTheDocument()
})

test('a card warns as the admin pane does', () => {
  render(SecretsView, { props: { data } })
  const open = screen.getByRole('button', { name: 'open' })
  expect(open.querySelector('.pill.warn')).toHaveTextContent('any site')
  expect(screen.getByRole('button', { name: 'admin' }).querySelector('.pill.warn')).toBeNull()
})

test('a card opens that one secret in place, and back returns to the list', async () => {
  const { container } = render(SecretsView, { props: { data } })
  await fireEvent.click(screen.getByRole('button', { name: 'admin' }))
  expect(screen.queryByRole('list')).toBeNull()
  const card = container.querySelector('.card.secret')!
  expect(card).toHaveTextContent('admin')
  expect(card).toHaveTextContent('password · env ADMIN_PW')
  expect(card).toHaveTextContent('allowedhttps://example.test/')
  expect(card).toHaveTextContent('fromconfig · /etc/c.yaml')
  await fireEvent.click(screen.getByRole('button', { name: '← All secrets' }))
  expect(screen.getAllByRole('listitem')).toHaveLength(2)
})

test('the cards open without a server call: they work with no onshow', async () => {
  const { container } = render(SecretsView, { props: { data } })
  await fireEvent.click(screen.getByRole('button', { name: 'open' }))
  expect(container.querySelector('.card.secret')).toHaveTextContent('open')
})

test('no secrets: one line, no scroller', () => {
  render(SecretsView, { props: { data: { workspace: 's', count: 0, secrets: [] } } })
  expect(screen.getByText('No secrets are configured.')).toBeInTheDocument()
  expect(screen.queryByRole('list')).toBeNull()
})
