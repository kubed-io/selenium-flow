import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import FileView from './FileView.svelte'

const entry = (name: string, content_type: string, folder = 'recordings', extra = {}) => ({
  name, size: 18_400_000, created: Date.now() - 60_000, content_type,
  image: content_type.startsWith('image/'),
  uri: `workspace://files/${folder}/${encodeURIComponent(name)}`,
  url: 'https://flow.example.com/f/' + encodeURIComponent(name), ...extra,
})

test('a recording: name, readable URI, REC, a player, facts and Open', () => {
  const data = entry('run 1.mp4', 'video/mp4')
  const { container } = render(FileView, { props: { data, uri: data.uri } })
  expect(screen.getByText('run 1.mp4')).toBeInTheDocument()
  expect(screen.getByText('workspace://files/recordings/run 1.mp4')).toBeInTheDocument()
  expect(screen.getByText('● REC')).toHaveClass('pill')
  expect(container.querySelector('video')).toHaveAttribute('src', data.url)
  expect(screen.getByText(/17\.5 MB/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Open' })).toHaveAttribute('href', data.url)
})

test('the player reports its length once it knows it', async () => {
  const data = entry('run.mp4', 'video/mp4')
  const { container } = render(FileView, { props: { data, uri: data.uri } })
  const video = container.querySelector('video')!
  Object.defineProperty(video, 'duration', { value: 252 })
  await fireEvent(video, new Event('loadedmetadata'))
  expect(screen.getByText(/^4:12 · /)).toBeInTheDocument()
})

test('a screenshot is an image from its signed link, without REC', () => {
  const data = entry('a.png', 'image/png', 'screenshots')
  render(FileView, { props: { data, uri: data.uri } })
  expect(screen.getByRole('img', { name: 'a.png' })).toHaveAttribute('src', data.url)
  expect(screen.queryByText('● REC')).toBeNull()
})

test('a download is a card with its glyph and its type', () => {
  const data = entry('report.csv', 'text/csv', 'downloads')
  render(FileView, { props: { data, uri: data.uri } })
  expect(screen.getByText('📊')).toBeInTheDocument()
  expect(screen.getByText(/text\/csv/)).toBeInTheDocument()
})

test('a link that will not load says why, in place of the preview', async () => {
  const data = entry('a.png', 'image/png', 'screenshots')
  render(FileView, { props: { data, uri: data.uri } })
  await fireEvent.error(screen.getByRole('img', { name: 'a.png' }))
  expect(screen.getByText(/This link has expired, or this server's address is not reachable from here/)).toBeInTheDocument()
  expect(screen.queryByRole('img')).toBeNull()
})

test('Open goes through the host when it can open links', async () => {
  const onlink = vi.fn()
  const data = entry('report.csv', 'text/csv', 'downloads')
  render(FileView, { props: { data, uri: data.uri, onlink } })
  await fireEvent.click(screen.getByRole('link', { name: 'Open' }))
  expect(onlink).toHaveBeenCalledWith(data.url)
})
