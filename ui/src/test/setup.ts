import { vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { webcrypto } from 'node:crypto'

// jsdom has neither `matchMedia` nor `Element.prototype.animate`. `motion.ts`
// reads `matchMedia('(prefers-reduced-motion: reduce)')` once, at import
// time, so stubbing it here — before any test file's own imports resolve —
// makes every component's `ms()` collapse to 0 for the rest of that file.
// Svelte's transition runtime skips calling `element.animate()` for an
// all-zero (duration and delay) transition, except an outro with a `css`
// function, which makes one zero-length call (see the `animate` stub below).
// `motion.test.ts` restubs this and calls
// `vi.resetModules()` to exercise the non-reduced branch for real.
vi.stubGlobal('matchMedia', (query: string) => ({ matches: query === '(prefers-reduced-motion: reduce)' }))

// `animate:flip` (FileGrid) calls `Element.prototype.getAnimations()` from its
// `fix()` helper when a tile leaves the grid. That read runs whatever the
// duration, to see whether an animation is already in flight, and jsdom does
// not implement it, so it needs its own stub (the `animate` stub is below).
if (!Element.prototype.getAnimations) {
  Element.prototype.getAnimations = () => []
}

// svelte 5.59.2+ makes one zero-length `element.animate()` call for an outro with
// a `css` function (fade, slide), even at duration 0. jsdom has no `animate`,
// so give it an inert Animation. The call is harmless: svelte calls `finish()`
// on the zero-length outro itself, right after it.
if (!Element.prototype.animate) {
  Element.prototype.animate = () => ({ cancel() {}, onfinish: null, currentTime: 0 }) as unknown as Animation
}

// jsdom has `crypto.getRandomValues` but no `crypto.subtle`, which PKCE needs.
// Node's own, defined rather than stubbed: `unstubGlobals` would remove a stub
// after the first test (spec 2026-10-09-admin-oidc, Verify first 2).
if (!globalThis.crypto?.subtle) {
  Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
}
