import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import Lightbox from './Lightbox.svelte'

const a = { name: 'a.png', size: 1, url: '/a', image: true }
const b = { name: 'b.pdf', size: 1, url: '/b', content_type: 'application/pdf' }
const c = { name: 'c.zip', size: 1, url: '/c' }

test('steps through the row it was opened on, stopping at the ends (X1, X2)', async () => {
  render(Lightbox, { files: [a, b, c], index: 0, base: '/r', onclose: () => {} })
  expect(screen.getByText('1 / 3')).toBeInTheDocument()
  expect(screen.getByText('‹ Prev')).toBeDisabled()
  expect(screen.getByRole('img')).toHaveAttribute('src', '/r/a')
  await fireEvent.keyDown(document, { key: 'ArrowRight' })
  expect(screen.getByTitle('b.pdf').tagName).toBe('IFRAME')
  await fireEvent.click(screen.getByText('Next ›'))
  expect(screen.getByText('No preview for this kind of file.')).toBeInTheDocument()
  expect(screen.getByText('Next ›')).toBeDisabled()
  expect(screen.getAllByText('Download')[0]).toHaveAttribute('download', 'c.zip')
})

test('Esc, Close and the backdrop close it; keys are ignored under a modal (X2)', async () => {
  const onclose = vi.fn()
  const { container } = render(Lightbox, { files: [a, b], index: 0, onclose })
  const modal = document.createElement('div'); modal.className = 'modal'; document.body.append(modal)
  await fireEvent.keyDown(document, { key: 'ArrowRight' })
  await fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.getByText('1 / 2')).toBeInTheDocument()
  expect(onclose).not.toHaveBeenCalled()
  modal.remove()
  await fireEvent.keyDown(document, { key: 'Escape' })
  await fireEvent.click(screen.getByText('Close'))
  await fireEvent.click(container.querySelector('.lightbox')!)
  expect(onclose).toHaveBeenCalledTimes(3)
})

test('the action moves on: the refreshed list at the same index, clamped (X3)', async () => {
  const run = vi.fn(async () => {})
  const refresh = vi.fn(async (at: number) => ({ files: [a, c], index: at }))
  render(Lightbox, { files: [a, b, c], index: 1, action: { label: '📌 Keep', run }, refresh, onclose: () => {} })
  await fireEvent.click(screen.getByText('📌 Keep'))
  await vi.waitFor(() => expect(screen.getByText('2 / 2')).toBeInTheDocument())
  expect(run).toHaveBeenCalledWith(b)
  expect(screen.getByText('📌 Keep')).not.toBeDisabled()
})

test('an empty refresh closes; cancelled is silent; any other failure alerts (X3)', async () => {
  const alert = vi.fn(); vi.stubGlobal('alert', alert)
  const onclose = vi.fn()
  const r1 = render(Lightbox, { files: [a], index: 0, action: { label: 'Go', run: async () => {} }, refresh: async () => ({ files: [], index: 0 }), onclose })
  await fireEvent.click(screen.getByText('Go'))
  await vi.waitFor(() => expect(onclose).toHaveBeenCalled())
  r1.unmount()
  render(Lightbox, { files: [a], index: 0, action: { label: 'No', run: async () => { throw new Error('cancelled') } }, onclose: () => {} })
  await fireEvent.click(screen.getByText('No'))
  await vi.waitFor(() => expect(screen.getByText('No')).not.toBeDisabled())
  expect(alert).not.toHaveBeenCalled()
})
