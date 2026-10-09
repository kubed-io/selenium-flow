import { describe, expect, test } from 'vitest'
import { ago, browserMark, bytes, countsText, flowParams, glyphFor, metaLine, plural, safeHref, workspaceLabel, stepSummary } from './format'

describe('format', () => {
  test('counts name recordings, singular and plural (R8)', () => {
  expect(countsText({ key: 'k', counts: { downloads: 0, screenshots: 6, recordings: 2, files: 2 } })).toBe('6 screenshots · 2 recordings · 2 files')
  expect(countsText({ key: 'k', counts: { downloads: 0, screenshots: 0, recordings: 1, files: 0 } })).toBe('1 recording')
})

test('bytes', () => {
    expect(bytes(512)).toBe('512 B')
    expect(bytes(2048)).toBe('2.0 KB')
    expect(bytes(3 * 1048576)).toBe('3.0 MB')
    expect(bytes(Number.NaN)).toBe('')
  })
  test('ago', () => {
    const now = 1_000_000_000
    expect(ago(0, now)).toBe('')
    expect(ago(now - 5_000, now)).toBe('5s ago')
    expect(ago(now - 120_000, now)).toBe('2m ago')
    expect(ago(now - 7_200_000, now)).toBe('2h ago')
  })
  test('browserMark is keyed on the browser actually running', () => {
    expect(browserMark('chrome')).toBe('🟢')
    expect(browserMark('Firefox')).toBe('🦊')
    expect(browserMark('msedge')).toBe('🌊')
    expect(browserMark('safari')).toBe('🧭')
    expect(browserMark(null)).toBe('🌐')
  })
  test('glyphFor never finds what Object gave the map', () => {
    expect(glyphFor('a.PDF')).toBe('📄')
    expect(glyphFor('noext')).toBe('📁')
    expect(glyphFor('x.constructor')).toBe('📁')
  })
  test('safeHref only lets http(s) through', () => {
    expect(safeHref('https://a.b/c')).toBe('https://a.b/c')
    expect(safeHref('javascript:alert(1)')).toBe('')
    expect(safeHref(null)).toBe('')
  })
  test('countsText says each folder in its own word, and falls back to files_count', () => {
    expect(countsText({ key: 'k', counts: { downloads: 1, screenshots: 2, files: 0 } })).toBe('1 download · 2 screenshots')
    expect(countsText({ key: 'k', files_count: 1 })).toBe('1 file')
    expect(countsText({ key: 'k' })).toBe('')
  })
  test('metaLine', () => {
    const now = 1_000_000_000
    expect(metaLine({ key: 'k', browser: 'chrome', version: '140', counts: { downloads: 0, screenshots: 1, files: 0 }, started: (now - 60_000) / 1000, node: 'n1' }, now))
      .toBe('chrome 140 · 1 screenshot · 1m ago · n1')
  })
  test('workspaceLabel', () => {
    expect(workspaceLabel({ key: 'k', name: 'mine' })).toBe('mine')
    expect(workspaceLabel({ key: 'k', owner: 'stdio' })).toBe('stdio')
    expect(workspaceLabel({ key: 'k' })).toBe('workspace')
  })
  test('stepSummary is the first string argument, cut at 60', () => {
    expect(stepSummary({ url: 'https://example.com/a' })).toBe('https://example.com/a')
    expect(stepSummary({ timeout: 3, text: 'hello' })).toBe('hello')
    expect(stepSummary({ selector: { css: '#go' }, action: 'click' })).toBe('#go')
    expect(stepSummary({ n: 1 })).toBe('')
    expect(stepSummary(undefined)).toBe('')
    const long = stepSummary({ url: 'x'.repeat(100) })
    expect(long).toHaveLength(60)
    expect(long.endsWith('…')).toBe(true)
  })
  test('plural', () => {
    expect(plural(0, 'step')).toBe('0 steps')
    expect(plural(1, 'step')).toBe('1 step')
    expect(plural(2, 'param')).toBe('2 params')
    expect(plural(1, 'kept file')).toBe('1 kept file')
  })
  test('flowParams reads a hand-edited parameters block without trusting it', () => {
    expect(flowParams({ properties: { a: { type: 'string' } }, required: ['a', 3] })).toEqual({ properties: { a: { type: 'string' } }, required: ['a'] })
    expect(flowParams({ properties: 'abc', required: 'email' })).toEqual({ properties: {}, required: [] })
    expect(flowParams({ properties: ['x'], required: 3 })).toEqual({ properties: {}, required: [] })
    expect(flowParams(null)).toEqual({ properties: {}, required: [] })
    expect(flowParams('x')).toEqual({ properties: {}, required: [] })
  })
})

test('metaLine ends with who opened the browser', () => {
  expect(metaLine({ key: 'k', browser: 'chrome', opened_by: { kind: 'oidc', username: 'drk' } })).toBe('chrome · by drk')
  expect(metaLine({ key: 'k', browser: 'chrome', opened_by: { kind: 'admin', username: null } })).toBe('chrome · by token')
  expect(metaLine({ key: 'k', browser: 'chrome', opened_by: null })).toBe('chrome')
})
