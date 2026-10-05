import { render, screen } from '@testing-library/svelte'
import { expect, test } from 'vitest'
import FlowView from './FlowView.svelte'

const steps = Array.from({ length: 8 }, (_, i) => ({ tool: 'navigate', args: { url: `https://example.com/${i}` } }))
const data = {
  name: 'login', description: 'Sign in',
  parameters: { type: 'object', required: ['user'], properties: { user: { type: 'string' }, retries: { type: 'integer', default: 3 } } },
  steps: [{ tool: 'interact', args: { selector: { css: '#go' }, action: 'click' }, onError: 'continue', id: 'go' }, ...steps.slice(1)],
}

test('name, description, parameters', () => {
  render(FlowView, { props: { data } })
  expect(screen.getByText('login')).toBeInTheDocument()
  expect(screen.getByText('Sign in')).toBeInTheDocument()
  expect(screen.getByText('user')).toBeInTheDocument()
  expect(screen.getByText('required')).toBeInTheDocument()
  expect(screen.getByText('retries')).toBeInTheDocument()
  expect(screen.getByText('integer')).toBeInTheDocument()
  expect(screen.getByText('= 3')).toBeInTheDocument()
})

test('the first 6 steps, onError marked, +N more', () => {
  const { container } = render(FlowView, { props: { data } })
  const items = container.querySelectorAll('ol li')
  expect(items).toHaveLength(6)
  expect(items[0]).toHaveTextContent('1. interact — #go')
  expect(items[0]).toHaveTextContent('onError: continue')
  expect(items[1]).toHaveTextContent('2. navigate — https://example.com/1')
  expect(screen.getByText('+2 more')).toBeInTheDocument()
})

test('expanded shows every step', () => {
  const { container } = render(FlowView, { props: { data, expanded: true } })
  expect(container.querySelectorAll('ol li')).toHaveLength(8)
  expect(screen.queryByText(/more/)).toBeNull()
})
