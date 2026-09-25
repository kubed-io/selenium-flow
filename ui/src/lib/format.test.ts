import { describe, expect, test } from 'vitest'
import { ago, browserMark, bytes, countsText, glyphFor, metaLine, safeHref, sessionLabel } from './format'

describe('format', () => {
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
  test('sessionLabel', () => {
    expect(sessionLabel({ key: 'k', name: 'mine' })).toBe('mine')
    expect(sessionLabel({ key: 'k', owner: 'stdio' })).toBe('stdio')
    expect(sessionLabel({ key: 'k' })).toBe('session')
  })
})
