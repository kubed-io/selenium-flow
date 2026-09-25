import { fireEvent, render } from '@testing-library/svelte'
import { afterEach, expect, test, vi } from 'vitest'
import Harness from './SectionHarness.test.svelte'

// A real, nonzero duration for this file only — every other test relies on
// `test/setup.ts`'s reduced-motion stub collapsing `ms()` to 0, which would
// make the outro finish in the same flush as the click and leave nothing to
// observe here.
vi.mock('../motion', () => ({ ms: () => 150 }))

afterEach(() => {
  delete (Element.prototype as { animate?: unknown }).animate
})

// jsdom has no `Element.prototype.animate`, so a nonzero duration can't run
// to completion on its own here — which is exactly what lets this test hold
// the outro open and drive it by hand, rather than racing whatever finishes
// it for real. The stub below never calls its own `onfinish`, so Svelte's
// automatic 'outroend' dispatch never happens on its own; the test dispatches
// it itself once it wants the outro to "finish".
test('data-open follows the outro ending, not the click that starts it', async () => {
  Element.prototype.animate = (() => ({ cancel() {}, onfinish: null, currentTime: 0 })) as unknown as typeof Element.prototype.animate

  const { container } = render(Harness, {})
  const section = container.querySelector('section')!
  const toggle = container.querySelector('button.title') as HTMLButtonElement
  const wrapper = container.querySelector('#myBody > div') as HTMLElement

  expect(section).toHaveAttribute('data-open', 'true')
  await fireEvent.click(toggle)
  // The click closed it (aria-expanded moves immediately) but the slide's
  // outro is still "running" — nothing has told the section its body is
  // actually gone yet.
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
  expect(section).toHaveAttribute('data-open', 'true')

  await fireEvent(wrapper, new Event('outroend'))
  expect(section).toHaveAttribute('data-open', 'false')
})
