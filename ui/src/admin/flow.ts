/* A stored flow is a file a person edits (§F1.6) and nothing validates its
   shape on the way out, so `steps: {}` and `parameters: 1` reach the panel —
   the screen you open in order to reach the editor and fix them. */
export const mapping = (v: unknown): Record<string, unknown> =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {}

export const listed = (v: unknown): unknown[] => (Array.isArray(v) ? v : [])

/* Keys come from that same YAML, so `tool: constructor` is reachable. */
export const own = <T>(map: Record<string, T>, key: string): T | undefined =>
  Object.hasOwn(map, key) ? map[key] : undefined

/* One glyph per action a step may dispatch to (`flowrun.RUNNABLE`); anything
   else is refused by the runner, and its ❓ is meant to look wrong. */
export const TOOL_ICON: Record<string, string> = {
  navigate: '🧭', interact: '👆', drag: '🤜', write: '✍️', press_key: '⌨️',
  extract: '🔍', execute_script: '📜', screenshot: '📷', frame: '🪟', resize: '📐',
  dialog: '💬', upload_file: '📤', print: '🖨️', assert: '✅',
}

/* JSON types already have glyphs everyone reads; at 12px an emoji is a smudge. */
export const TYPE_ICON: Record<string, string> = {
  string: '"', number: '#', integer: '#', boolean: '?', object: '{}', array: '[]',
}

/* Only `write` has a `secret` argument; it types what nobody can see. */
export const bindsSecret = (step: unknown): boolean => !!mapping(mapping(step).args).secret

/* A selector carries exactly one of css/xpath (§F2.14); the run report
   unwraps it the same way (`flows.run.summarise`). */
export function selectorText(v: unknown): string | null {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return null
  const o = v as Record<string, unknown>
  const named = ['css', 'xpath'].filter((k) => own(o, k))
  return named.length === 1 && Object.keys(o).length === 1 ? named[0] + ' ' + o[named[0]] : null
}

export function argValue(key: string, v: unknown): string {
  if (key === 'secret') {
    const s = mapping(v)
    return s.name ? s.name + ' / ' + s.key : 'a secret'
  }
  const selector = selectorText(v)
  if (selector !== null) return selector
  return v !== null && typeof v === 'object' ? JSON.stringify(v) : String(v)
}
