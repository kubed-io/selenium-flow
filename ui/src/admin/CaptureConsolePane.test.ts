import { fireEvent, render, screen } from '@testing-library/svelte'
import { beforeEach, expect, test, vi } from 'vitest'
import { consoleData, FakeEventSource, stubEventSource } from '../test/capture'
import CaptureConsolePane from './CaptureConsolePane.svelte'

const api = vi.fn()
beforeEach(() => { api.mockReset(); api.mockResolvedValue(consoleData); stubEventSource() })
const props = () => ({ api, key: 'ws', root: '' })

test('rows are text, never markup, and the footer reads as the board does', async () => {
  const { container } = render(CaptureConsolePane, { props: props() })
  expect(await screen.findByText('boom <b>x</b>')).toBeInTheDocument()
  expect(container.querySelector('b')).toBeNull()
  expect(screen.getByText('app.js:3')).toBeInTheDocument()
  expect(screen.getByText('3 messages · 2 debug not kept · workspace://console')).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: /Errors/ }))
  expect(screen.getByText('1 of 3 messages · workspace://console')).toBeInTheDocument()
})

test('Clear hides what was there and says nothing was sent', async () => {
  render(CaptureConsolePane, { props: props() })
  await fireEvent.click(await screen.findByRole('button', { name: 'Clear' }))
  expect(screen.getByText(/^Cleared at /)).toBeInTheDocument()
  expect(screen.getByText('Entries that arrive from now on show here. The browser keeps running; nothing was sent to it.')).toBeInTheDocument()
  expect(api).toHaveBeenCalledTimes(1)
})

test('Live opens the stream, Paused counts, Captured closes it and reloads', async () => {
  render(CaptureConsolePane, { props: props() })
  await fireEvent.click(await screen.findByRole('button', { name: 'Live' }))
  const es = FakeEventSource.last
  expect(es.url).toContain('kind=console')
  es.emit({ seq: 9, at: 1, level: 'log', text: 'a', kept: true })
  expect(await screen.findByText('Live · 1 messages')).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
  es.emit({ seq: 10, at: 2, level: 'log', text: 'b', kept: true })
  expect(await screen.findByText('Paused · 1 messages · 1 new')).toBeInTheDocument()
  es.emit({ dropped: 3 })
  expect(await screen.findByText(/3 dropped/)).toHaveClass('warn')
  await fireEvent.click(screen.getByRole('button', { name: 'Captured' }))
  expect(es.closed).toBe(true)
  expect(api).toHaveBeenCalledTimes(2)
})

test('unmounting closes the stream', async () => {
  const r = render(CaptureConsolePane, { props: props() })
  await fireEvent.click(await screen.findByRole('button', { name: 'Live' }))
  const es = FakeEventSource.last
  r.unmount()
  expect(es.closed).toBe(true)
})

test('empty, and capture off', async () => {
  api.mockResolvedValue({ ...consoleData, entries: [], count: 0, total: 0 })
  const r = render(CaptureConsolePane, { props: props() })
  expect(await screen.findByText('No messages since the last navigation')).toBeInTheDocument()
  r.unmount()
  api.mockResolvedValue({ capture: false, how: 'open_session(capture=true)' })
  render(CaptureConsolePane, { props: props() })
  expect(await screen.findByText('Capture is off for this workspace. Open the browser with open_session(capture=true).')).toBeInTheDocument()
})

test('a source is a file name and line, never the query or the whole URL', async () => {
  api.mockResolvedValue({ ...consoleData, entries: [
    { ...consoleData.entries[0], seq: 7, source: { url: 'https://example.com/', line: 2 } },
    { ...consoleData.entries[0], seq: 8, source: { url: 'https://a.test/app.js?v=1', line: 9 } },
  ] })
  render(CaptureConsolePane, { props: props() })
  expect(await screen.findByText('example.com:2')).toBeInTheDocument()
  expect(screen.getByText('app.js:9')).toBeInTheDocument()
})
