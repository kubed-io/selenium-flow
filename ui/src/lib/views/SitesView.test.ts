import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import SitesView from './SitesView.svelte'

const data = {
  uri: 'workspace://site-data', saved_at: Date.now() / 1000,
  sites: [
    { site: 'app.example.com', uri: 'workspace://site-data/app.example.com', cookies: 2, storage: [{ origin: 'https://app.example.com', local_storage: 2, session_storage: 0 }] },
    { site: 'docs.example.com', uri: 'workspace://site-data/docs.example.com', cookies: 5, storage: [] },
  ],
}

test('one row per host with its counts; a row drills into the site', async () => {
  const onshow = vi.fn()
  render(SitesView, { props: { data, onshow } })
  expect(screen.getByText('Site data')).toBeInTheDocument()
  expect(screen.getByText('workspace://site-data')).toBeInTheDocument()
  expect(screen.getByText(/^saved /)).toBeInTheDocument()
  expect(screen.getByText('2 cookies · 2 local · 0 session')).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: /docs\.example\.com/ }))
  expect(onshow).toHaveBeenCalledWith('workspace://site-data/docs.example.com')
})

test('without onshow the rows are not buttons', () => {
  render(SitesView, { props: { data } })
  expect(screen.queryByRole('button')).toBeNull()
  expect(screen.getAllByTitle("Open isn't available in this client")).toHaveLength(2)
})

test('nothing saved says how to save', () => {
  render(SitesView, { props: { data: { uri: 'workspace://site-data', saved_at: null, sites: [] } } })
  expect(screen.getByText(/Nothing saved/)).toBeInTheDocument()
  expect(screen.queryByText(/^saved /)).toBeNull()
})
