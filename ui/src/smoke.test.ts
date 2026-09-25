import { render, screen } from '@testing-library/svelte'
import { expect, test } from 'vitest'
import Admin from './admin/Admin.svelte'

test('a component renders under jsdom', () => {
  render(Admin, { mount: '/flow', console: '/' })
  expect(screen.getByText('selenium-flow')).toHaveAttribute('data-mount', '/flow')
})
