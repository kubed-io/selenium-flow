import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import Harness from './ModalHarness.test.svelte'

test('title, escaped body, Cancel and the confirm (M1)', () => {
  const { container } = render(Harness, { onconfirm: () => {}, onclosed: () => {}, danger: true })
  expect(container.querySelector('.modal .sheet .head')).toHaveTextContent('Clear <all>')
  expect(screen.getByText('a <b>').tagName).toBe('LI')
  expect(screen.getByText('Clear 2 files')).toHaveClass('danger')
  expect(screen.getByText('Cancel')).toBeInTheDocument()
})

test('Esc, the backdrop and Cancel all take the cancel path (M1)', async () => {
  for (const how of ['esc', 'backdrop', 'cancel'] as const) {
    const oncancel = vi.fn(), onclosed = vi.fn()
    const r = render(Harness, { onconfirm: () => {}, oncancel, onclosed })
    if (how === 'esc') await fireEvent.keyDown(document, { key: 'Escape' })
    if (how === 'backdrop') await fireEvent.click(r.container.querySelector('.modal')!)
    if (how === 'cancel') await fireEvent.click(screen.getByText('Cancel'))
    expect(oncancel).toHaveBeenCalledOnce()
    expect(onclosed).toHaveBeenCalledWith(false)
    r.unmount()
  }
})

test('confirm is disabled while running; success closes without cancelling (M1)', async () => {
  let finish!: () => void
  const oncancel = vi.fn(), onclosed = vi.fn()
  render(Harness, { onconfirm: () => new Promise<void>((r) => { finish = r }), oncancel, onclosed })
  await fireEvent.click(screen.getByText('Clear 2 files'))
  expect(screen.getByText('Clear 2 files')).toBeDisabled()
  finish(); await vi.waitFor(() => expect(onclosed).toHaveBeenCalledWith(true))
  expect(oncancel).not.toHaveBeenCalled()
})

test('a failure stays open, re-arms, and alerts (M1)', async () => {
  const alert = vi.fn(); vi.stubGlobal('alert', alert)
  const onclosed = vi.fn()
  render(Harness, { onconfirm: async () => { throw new Error('nope') }, onclosed })
  await fireEvent.click(screen.getByText('Clear 2 files'))
  await vi.waitFor(() => expect(alert).toHaveBeenCalledWith('nope'))
  expect(screen.getByText('Clear 2 files')).not.toBeDisabled()
  expect(onclosed).not.toHaveBeenCalled()
})

test('a cancel that races a slow confirm still only closes once (fix round 1, Important)', async () => {
  let finish!: () => void
  const oncancel = vi.fn(), onclosed = vi.fn()
  render(Harness, { onconfirm: () => new Promise<void>((r) => { finish = r }), oncancel, onclosed })
  await fireEvent.click(screen.getByText('Clear 2 files'))
  await fireEvent.click(screen.getByText('Cancel'))
  expect(oncancel).toHaveBeenCalledOnce()
  expect(onclosed).toHaveBeenCalledTimes(1)
  expect(onclosed).toHaveBeenCalledWith(false)
  finish()
  // A real macrotask tick, not `vi.waitFor`: the assertion below is already
  // true the instant `finish()` returns (the `await spec.onconfirm()` inside
  // `confirm()` hasn't resumed yet), so `vi.waitFor` would resolve on its
  // first, eager check without ever giving that continuation a turn — a
  // negative assertion needs to wait past that turn, not poll until it's met.
  await new Promise((r) => setTimeout(r, 0))
  expect(onclosed).toHaveBeenCalledTimes(1)
  expect(onclosed).toHaveBeenCalledWith(false)
})
