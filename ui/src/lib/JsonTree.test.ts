import { render, screen } from '@testing-library/svelte'
import { expect, test } from 'vitest'
import JsonTree from './JsonTree.svelte'

test('objects and arrays fold with a count; the top is open, the rest shut', () => {
  const { container } = render(JsonTree, { props: { value: { title: 'Flow', steps: [1, 2], timeout: 30, shared: false, x: null } } })
  const folds = container.querySelectorAll('details')
  expect(folds).toHaveLength(2)
  expect(folds[0]).toHaveAttribute('open')
  expect(folds[1]).not.toHaveAttribute('open')
  expect(folds[0].querySelector('summary')).toHaveTextContent('{5}')
  expect(folds[1].querySelector('summary')).toHaveTextContent('steps [2]')
  expect(screen.getByText('"Flow"')).toBeInTheDocument()
  expect(screen.getByText('30')).toBeInTheDocument()
  expect(screen.getByText('false')).toBeInTheDocument()
  expect(screen.getByText('null')).toBeInTheDocument()
})

test('a scalar alone is a leaf', () => {
  render(JsonTree, { props: { value: 'just text' } })
  expect(screen.getByText('"just text"')).toBeInTheDocument()
})
