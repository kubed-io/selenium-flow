import { expect, test } from 'vitest'
import { argValue, bindsSecret, listed, mapping, own, selectorText, TOOL_ICON, TYPE_ICON } from './flow'

test('mapping and listed say what a thing has to be to be used as one (W8)', () => {
  expect(mapping({ a: 1 })).toEqual({ a: 1 })
  expect(mapping([1])).toEqual({})
  expect(mapping(null)).toEqual({})
  expect(listed([1])).toEqual([1])
  expect(listed({})).toEqual([])
})

test('own never finds what Object gave the map (W8)', () => {
  expect(own(TOOL_ICON, 'navigate')).toBe('🧭')
  expect(own(TOOL_ICON, 'constructor')).toBeUndefined()
  expect(own(TOOL_ICON, 'toString')).toBeUndefined()
})

test('a selector reads as one expression (W3)', () => {
  expect(selectorText({ css: 'button.go' })).toBe('css button.go')
  expect(selectorText({ xpath: '//a' })).toBe('xpath //a')
  expect(selectorText({ css: 'a', xpath: '//a' })).toBeNull()
  expect(selectorText('css a')).toBeNull()
})

test('argValue: a secret by reference, a selector unwrapped, objects as JSON', () => {
  expect(argValue('secret', { name: 'admin', key: 'token' })).toBe('admin / token')
  expect(argValue('secret', {})).toBe('a secret')
  expect(argValue('selector', { css: 'a' })).toBe('css a')
  expect(argValue('options', { a: 1 })).toBe('{"a":1}')
  expect(argValue('url', '${admin}/')).toBe('${admin}/')
  expect(argValue('n', null)).toBe('null')
})

test('bindsSecret', () => {
  expect(bindsSecret({ args: { secret: { name: 'a', key: 'b' } } })).toBe(true)
  expect(bindsSecret({ args: {} })).toBe(false)
  expect(bindsSecret(null)).toBe(false)
})

test('TYPE_ICON has a glyph for every JSON type', () => {
  expect(own(TYPE_ICON, 'string')).toBe('"')
  expect(own(TYPE_ICON, 'number')).toBe('#')
  expect(own(TYPE_ICON, 'integer')).toBe('#')
  expect(own(TYPE_ICON, 'boolean')).toBe('?')
  expect(own(TYPE_ICON, 'object')).toBe('{}')
  expect(own(TYPE_ICON, 'array')).toBe('[]')
})
