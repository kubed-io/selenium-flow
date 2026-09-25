/* Polish only (spec, difference 3): never longer than 150 ms, and none at all
   for someone who asked their system for less motion. */
const reduce = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches

export const ms = (duration: number): number => (reduce ? 0 : Math.min(duration, 150))
