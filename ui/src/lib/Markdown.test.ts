import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import Markdown from './Markdown.svelte'
import { body, parse } from './markdown'

test('front matter is not drawn', () => {
  expect(body('---\nname: x\ndescription: y\n---\n\n# T\n')).toBe('\n# T\n')
  expect(body('# T\n')).toBe('# T\n')
  expect(body('---\nunterminated')).toBe('---\nunterminated')
})

test('the first # heading is the title; spaces are not blocks', () => {
  const doc = parse('---\nname: x\n---\n\n# When something goes wrong\n\nRead the error.\n\n## A timeout\n')
  expect(doc.title?.text).toBe('When something goes wrong')
  expect(doc.blocks.map((b) => b.type)).toEqual(['paragraph', 'heading'])
})

test('no # heading, no title', () => {
  expect(parse('Just text.').title).toBeNull()
})

const BASE = 'skill://selenium-flow/references/TROUBLESHOOTING.md'
const draw = (text: string, props = {}) => render(Markdown, { props: { tokens: parse(text).blocks, base: BASE, ...props } })

test('the block types the skill uses', () => {
  const { container } = draw([
    '## A timeout', '', 'Suspect **the page** before *the expression*.', '',
    '- one', '- two', '', '1. first', '2. second', '',
    '```', 'extract(selector={"xpath": "//title"})', '```', '',
    '| Task | Read |', '|---|---|', '| a | b |', '', '> quoted', '', '---',
  ].join('\n'))
  expect(container.querySelector('h2')).toHaveTextContent('A timeout')
  expect(container.querySelector('strong')).toHaveTextContent('the page')
  expect(container.querySelector('em')).toHaveTextContent('the expression')
  expect(container.querySelectorAll('ul > li')).toHaveLength(2)
  expect(container.querySelectorAll('ol > li')).toHaveLength(2)
  expect(container.querySelector('pre code')).toHaveTextContent('extract(selector={"xpath": "//title"})')
  expect(container.querySelectorAll('th')).toHaveLength(2)
  expect(container.querySelector('td')).toHaveTextContent('a')
  expect(container.querySelector('blockquote')).toHaveTextContent('quoted')
  expect(container.querySelector('hr')).not.toBeNull()
})

test('raw HTML is text, never markup', () => {
  const { container } = draw('A <img src=x onerror=alert(1)> b\n\n<script>alert(1)</script>\n')
  expect(container.querySelector('img')).toBeNull()
  expect(container.querySelector('script')).toBeNull()
  expect(container).toHaveTextContent('<img src=x onerror=alert(1)>')
})

test('a URI in a code span drills through show', async () => {
  const onshow = vi.fn()
  draw('See `skill://selenium-flow/references/FLOWS.md` and `flow://flows/{name}`.', { onshow })
  await fireEvent.click(screen.getByRole('button', { name: 'skill://selenium-flow/references/FLOWS.md' }))
  expect(onshow).toHaveBeenCalledWith('skill://selenium-flow/references/FLOWS.md')
  expect(screen.queryByRole('button', { name: 'flow://flows/{name}' })).toBeNull()
  expect(screen.getByText('flow://flows/{name}').tagName).toBe('CODE')
})

test('without onshow a URI is just code', () => {
  draw('See `workspace://files`.')
  expect(screen.queryByRole('button')).toBeNull()
})

test('a relative link drills; a web link opens through the host', async () => {
  const onshow = vi.fn()
  const onlink = vi.fn()
  draw('[flows](FLOWS.md) and [the wiki](https://example.com/wiki) and [bad](javascript:alert(1))', { onshow, onlink })
  await fireEvent.click(screen.getByRole('button', { name: 'flows' }))
  expect(onshow).toHaveBeenCalledWith('skill://selenium-flow/references/FLOWS.md')
  const web = screen.getByRole('link', { name: 'the wiki' })
  expect(web).toHaveAttribute('href', 'https://example.com/wiki')
  await fireEvent.click(web)
  expect(onlink).toHaveBeenCalledWith('https://example.com/wiki')
  expect(screen.queryByRole('link', { name: 'bad' })).toBeNull()
  expect(screen.getByText('bad')).toBeInTheDocument()
})

test('a link whose text is a URI keeps one button, not two', () => {
  draw('[`skill://selenium-flow/references/FLOWS.md`](FLOWS.md)', { onshow: vi.fn() })
  expect(screen.getAllByRole('button')).toHaveLength(1)
})

test('link reference definitions are not blocks or text', () => {
  expect(parse('Hi [r].\n\n[r]: https://example.org/x\n').blocks.map((b) => b.type)).toEqual(['paragraph'])
  const { container } = render(Markdown, { props: { tokens: [{ type: 'def', raw: '[r]: x', tag: 'r', href: 'x' }] as never, base: BASE } })
  expect(container).not.toHaveTextContent('[r]')
})

test('task list items draw a box', () => {
  const { container } = draw('- [x] done\n- [ ] todo')
  expect(container.querySelectorAll('li')[0]).toHaveTextContent('☑ done')
  expect(container.querySelectorAll('li')[1]).toHaveTextContent('☐ todo')
})
