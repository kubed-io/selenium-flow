/* Whether each accordion Section is open, keyed by its id and living for the
   page's life — not per-mount. The static admin page never remounted a
   section, so a fold you closed stayed closed; SessionDetail remounts per
   session (`{#key route.key}`), and without this a fresh Section always
   starts `open`, undoing that on every session switch (parity, live deploy). */
export const folds = $state<Record<string, boolean>>({})

/** Test-only: clear accumulated state between tests so they can't leak into
    one another through this module-level store. */
export function resetFolds() {
  for (const key of Object.keys(folds)) delete folds[key]
}
