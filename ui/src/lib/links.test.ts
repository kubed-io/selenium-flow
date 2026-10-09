import { expect, test } from 'vitest'
import { showable, target } from './links'

const SKILL = 'skill://selenium-flow/SKILL.md'

test('a concrete URI on a server scheme is showable; a placeholder is not', () => {
  expect(showable('workspace://files')).toBe('workspace://files')
  expect(showable('skill://selenium-flow/references/FLOWS.md')).toBe('skill://selenium-flow/references/FLOWS.md')
  expect(showable('flow://flows/{name}')).toBeNull()
  expect(showable('flow://flows/<name>')).toBeNull()
  expect(showable('workspace://')).toBeNull()
  expect(showable('https://example.com/')).toBeNull()
  expect(showable('open_session')).toBeNull()
})

test('a relative link resolves against the document and drills', () => {
  expect(target('references/FLOWS.md', SKILL)).toEqual({ kind: 'show', uri: 'skill://selenium-flow/references/FLOWS.md' })
  expect(target('../SKILL.md', 'skill://selenium-flow/references/FLOWS.md')).toEqual({ kind: 'show', uri: SKILL })
  expect(target('FLOWS.md#parameters', 'skill://selenium-flow/references/WORKSPACES.md'))
    .toEqual({ kind: 'show', uri: 'skill://selenium-flow/references/FLOWS.md' })
})

test('the web opens; anything else is text', () => {
  expect(target('https://example.com/wiki', SKILL)).toEqual({ kind: 'open', url: 'https://example.com/wiki' })
  expect(target('javascript:alert(1)', SKILL)).toBeNull()
  expect(target('#top', SKILL)).toBeNull()
  expect(target('', SKILL)).toBeNull()
  expect(target('mailto:drk@example.com', SKILL)).toBeNull()
})
