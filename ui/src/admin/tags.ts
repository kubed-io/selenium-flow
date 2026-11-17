/* A flow's tags as the listing gives them, already read by the server
   (`flows.tags`). A filter is rows of tags: a flow passes when it carries every
   tag of any row, as the server reads one. A row with nothing picked yet
   narrows nothing, so a bar with no tags in it shows every flow. */

export const fold = (tag: string): string => tag.trim().toLowerCase()

const carried = (tags: unknown): string[] =>
  Array.isArray(tags) ? tags.filter((t): t is string => typeof t === 'string') : []

export function passes(rows: string[][], tags: unknown): boolean {
  const live = rows.filter((row) => row.length)
  if (!live.length) return true
  const has = new Set(carried(tags))
  return live.some((row) => row.every((t) => has.has(t)))
}

/* Each tag on the listed flows, with how many carry it, in tag order. */
export function inUse(flows: { tags?: unknown }[]): [string, number][] {
  const counts = new Map<string, number>()
  for (const f of flows) for (const t of carried(f.tags)) counts.set(t, (counts.get(t) ?? 0) + 1)
  return [...counts].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
}
