/* The hash, not the history API: every other path under this mount is a real
   endpoint, and a history route would need a catch-all that answers a mistyped
   API call with HTML. A hash never reaches the server. */
export type Route =
  | { view: 'list' }
  | { view: 'console' }
  | { view: 'secrets' }
  | { view: 'session'; key: string; tab: 'files' | 'flows'; flow: string | undefined }

export function parse(hash: string): Route {
  const [view, key, tab, flow] = hash.replace(/^#\/?/, '').split('/')
  if (view === 'console') return { view: 'console' }
  if (view === 'secrets') return { view: 'secrets' }
  if (view === 'sessions' && key) {
    return {
      view: 'session',
      key: decodeURIComponent(key),
      tab: tab === 'flows' ? 'flows' : 'files',
      flow: flow ? decodeURIComponent(flow) : undefined,
    }
  }
  return { view: 'list' }
}

const enc = encodeURIComponent
export const hashes = {
  list: '#/',
  console: '#/console',
  secrets: '#/secrets',
  session: (key: string) => '#/sessions/' + enc(key),
  flows: (key: string) => '#/sessions/' + enc(key) + '/flows',
  flow: (key: string, name: string) => '#/sessions/' + enc(key) + '/flows/' + enc(name),
}

export const router = $state<{ route: Route }>({ route: parse(location.hash) })

export const go = (hash: string) => { location.hash = hash }

/* In the address bar and the route, without a history entry and without
   hashchange: picking through a dozen flows should not take a dozen Backs. */
export function replace(hash: string) {
  history.replaceState(null, '', hash)
  router.route = parse(hash)
}

export function listen(): () => void {
  const on = () => { router.route = parse(location.hash) }
  window.addEventListener('hashchange', on)
  on()
  return () => window.removeEventListener('hashchange', on)
}
