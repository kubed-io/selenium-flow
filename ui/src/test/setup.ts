import { vi } from 'vitest'
import '@testing-library/jest-dom/vitest'

// jsdom has neither `matchMedia` nor `Element.prototype.animate`. `motion.ts`
// reads `matchMedia('(prefers-reduced-motion: reduce)')` once, at import
// time, so stubbing it here — before any test file's own imports resolve —
// makes every component's `ms()` collapse to 0 for the rest of that file.
// Svelte's transition runtime skips calling `element.animate()` entirely for
// an all-zero (duration and delay) transition, so component tests never hit
// jsdom's missing API. `motion.test.ts` restubs this and calls
// `vi.resetModules()` to exercise the non-reduced branch for real.
vi.stubGlobal('matchMedia', (query: string) => ({ matches: query === '(prefers-reduced-motion: reduce)' }))

// `animate:flip` (FileGrid) still calls `Element.prototype.getAnimations()`
// from its `fix()` helper when a tile leaves the grid — that check runs
// unconditionally, ahead of and regardless of any duration, to see whether an
// animation is already in flight. jsdom implements neither `getAnimations`
// nor `animate`; the zero-duration transitions above mean `animate()` itself
// is never called (Svelte's runtime skips it when duration and delay are both
// 0), so only this one read needs a stub.
if (!Element.prototype.getAnimations) {
  Element.prototype.getAnimations = () => []
}
