import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import Login from './Login.svelte'

test('no OIDC config, no OIDC row', () => {
  const { container } = render(Login, { onsubmit: vi.fn(), refused: false })
  expect(container.querySelector('#oidcSignIn')).toBeNull()
})

test('the OIDC row sits below the token form, as drawn', async () => {
  const start = vi.fn()
  const { container } = render(Login, { onsubmit: vi.fn(), refused: false, oidc: { framed: false, start } })
  const form = container.querySelector('#loginForm')!
  const button = container.querySelector('#oidcSignIn')!
  expect(form.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(button).toHaveTextContent('Sign in with OIDC')
  expect(screen.getByText('the configured issuer · needs the admin role')).toBeInTheDocument()
  await fireEvent.click(button)
  expect(start).toHaveBeenCalledOnce()
})

test('framed, the button opens the page in a new tab instead', async () => {
  const open = vi.fn()
  vi.stubGlobal('open', open)
  const start = vi.fn()
  const { container } = render(Login, { onsubmit: vi.fn(), refused: false, oidc: { framed: true, start } })
  await fireEvent.click(container.querySelector('#oidcSignIn')!)
  expect(open).toHaveBeenCalledWith(location.href, '_blank', 'noopener')
  expect(start).not.toHaveBeenCalled()
  expect(screen.getByText('opens in a new tab: the issuer will not load in a frame')).toBeInTheDocument()
})

test('an OIDC message takes the error line', () => {
  render(Login, { onsubmit: vi.fn(), refused: false, said: 'Your sign-in ended; sign in again.' })
  expect(screen.getByText('Your sign-in ended; sign in again.')).toBeVisible()
})
