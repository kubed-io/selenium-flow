import type { Secret } from './types'

// What a secret's card says about it, for the admin pane and the app alike:
// both draw the same catalogue entry, so they must never read it differently.

export const keyLabel = (s: Secret, k: string) => {
  const from = s.key_sources?.[k]
  if (!from || from.from === 'filesystem') return k
  if (from.from === 'env') return `${k} · env ${from.name}`
  return `${k} · ${from.from}`
}
// A list, not one pill: a secret with an inline or unresolved key and a leash
// that does not work needs ALL its warnings shown, not whichever one happened
// to be checked first. Order matches severity. A secret that carries `refused`
// cannot be used at all, whatever else is true of it.
export const warnsOf = (s: Secret) => [
  s.refused || s.allowed_urls_rejected ? 'unusable until fixed' : null,
  s.keys_unresolved?.length ? 'key unresolved' : null,
  s.inline_keys?.length ? 'inline value' : null,
].filter((w): w is string => w !== null)
// Both a broken leash AND unresolved keys can be true at once — show both,
// leash first, so the operator sees every reason the secret will fail. The
// server's own sentence for a refusal comes first, and says how to fix it.
export const reasonOf = (s: Secret) => [
  s.refused ?? null,
  s.allowed_urls_rejected ? 'allowed_urls: ' + ([] as string[]).concat(s.allowed_urls_rejected).join(', ') : null,
  ...(s.keys_unresolved?.map((u) => `${u.key}: ${u.reason}`) ?? []),
].filter((r): r is string => r !== null).join('; ')
export const fromOf = (s: Secret) => (s.origins ?? []).map((o) => `${o.source} · ${o.location}`).join(' + ')
