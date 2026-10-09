import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import FlowsView from './FlowsView.svelte'

const data = {
  workspace: 's', count: 2,
  flows: [
    { name: 'login', description: 'Sign in', parameters: { properties: { a: {}, b: {} } }, step_count: 3, shared: true },
    { name: 'a b/c', description: '', parameters: {}, step_count: 1, shared: false },
  ],
}

test('one card per flow in a list scroller', () => {
  render(FlowsView, { props: { data, onshow: () => {} } })
  expect(screen.getByRole('list')).toBeInTheDocument()
  expect(screen.getAllByRole('listitem')).toHaveLength(2)
  expect(screen.getByText('login')).toBeInTheDocument()
  expect(screen.getByText('Sign in')).toBeInTheDocument()
  expect(screen.getByText('2 params · 3 steps')).toBeInTheDocument()
  expect(screen.getByText('0 params · 1 step')).toBeInTheDocument()
  expect(screen.getAllByTitle('shared')).toHaveLength(1)
})

test('a card click shows the flow by its encoded uri', async () => {
  const onshow = vi.fn()
  render(FlowsView, { props: { data, onshow } })
  await fireEvent.click(screen.getByRole('button', { name: 'a b/c' }))
  expect(onshow).toHaveBeenCalledWith('flow://flows/a%20b%2Fc')
})

test('no flows: one line, no scroller', () => {
  render(FlowsView, { props: { data: { workspace: 's', count: 0, flows: [] } } })
  expect(screen.getByText('No flows yet.')).toBeInTheDocument()
  expect(screen.queryByRole('list')).toBeNull()
})

test('without onshow the cards are not buttons, and say so', () => {
  render(FlowsView, { props: { data } })
  expect(screen.queryByRole('button')).toBeNull()
  expect(screen.getAllByTitle("Open isn't available in this client")).toHaveLength(2)
})

test('a card that opens carries no unavailable tooltip', () => {
  render(FlowsView, { props: { data, onshow: () => {} } })
  expect(screen.queryByTitle("Open isn't available in this client")).toBeNull()
})

test('counts are pluralised, and odd parameters count as none', () => {
  const odd = { workspace: 's', count: 3, flows: [
    { name: 'one', parameters: { properties: { a: {} } }, step_count: 1 },
    { name: 'str', parameters: { properties: 'abc' }, step_count: 2 },
    { name: 'nil', parameters: null, step_count: 0 },
  ] }
  render(FlowsView, { props: { data: odd as never } })
  expect(screen.getByText('1 param · 1 step')).toBeInTheDocument()
  expect(screen.getByText('0 params · 2 steps')).toBeInTheDocument()
  expect(screen.getByText('0 params · 0 steps')).toBeInTheDocument()
})
