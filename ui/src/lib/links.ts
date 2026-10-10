import { safeHref } from './format'

/* The schemes this server serves: a URI on one of them is something `show`
   may draw. */
const SCHEMES = ['skill:', 'flow:', 'workspace:', 'secret:']
// Concrete: something after the //, and no placeholder or space in it.
const CONCRETE = /^(skill|flow|workspace|secret):\/\/[^\s{}<>]+$/

/* A code span's text, when the whole of it is a URI worth drilling into. */
export const showable = (text: string): string | null => (CONCRETE.test(text) ? text : null)

export type Target = { kind: 'show'; uri: string } | { kind: 'open'; url: string }

/* Where a markdown link goes, resolved against the document's own URI
   (spec 2026-10-09-show-everything, ruling 4). Null: draw it as text. */
export function target(href: string, base: string): Target | null {
  if (!href || href.startsWith('#')) return null
  let url: URL
  try {
    url = new URL(href, base)
  } catch {
    return null
  }
  if (SCHEMES.includes(url.protocol)) {
    url.hash = ''
    const uri = showable(url.href)
    return uri ? { kind: 'show', uri } : null
  }
  const open = safeHref(url.href)
  return open ? { kind: 'open', url: open } : null
}
