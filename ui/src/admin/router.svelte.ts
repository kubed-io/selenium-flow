/* The hash, not the history API: every other path under this mount is a real
   endpoint, and a history route would need a catch-all that answers a mistyped
   API call with HTML. A hash never reaches the server. */
export type Tab = 'files' | 'flows' | 'site-data' | 'history'
export type Route =
  | { view: 'list' }
  | { view: 'console' }
  | { view: 'secrets' }
  | { view: 'settings' }
  | { view: 'session'; key: string; tab: Tab; flow: string | undefined; site: string | undefined }

const TABS: readonly string[] = ['flows', 'site-data', 'history']

export function parse(hash: string): Route {
  const [view, key, tab, rest] = hash.replace(/^#\/?/, '').split('/')
  if (view === 'console') return { view: 'console' }
  if (view === 'secrets') return { view: 'secrets' }
  if (view === 'settings') return { view: 'settings' }
  if (view === 'sessions' && key) {
    // parse() runs at module load: a bad pasted `%` must not stop the app booting.
    try {
      const picked = (TABS.includes(tab) ? tab : 'files') as Tab
      const named = rest ? decodeURIComponent(rest) : undefined
      return {
        view: 'session',
        key: decodeURIComponent(key),
        tab: picked,
        // The fourth segment is a flow on Flows and a host on Site data.
        flow: picked === 'flows' ? named : undefined,
        site: picked === 'site-data' ? named : undefined,
      }
    } catch {
      return { view: 'list' }
    }
  }
  return { view: 'list' }
}

const enc = encodeURIComponent
export const hashes = {
  list: '#/',
  console: '#/console',
  secrets: '#/secrets',
  settings: '#/settings',
  session: (key: string) => '#/sessions/' + enc(key),
  flows: (key: string) => '#/sessions/' + enc(key) + '/flows',
  siteData: (key: string) => '#/sessions/' + enc(key) + '/site-data',
  site: (key: string, host: string) => '#/sessions/' + enc(key) + '/site-data/' + enc(host),
  history: (key: string) => '#/sessions/' + enc(key) + '/history',
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

/* The route, read from the address bar: at mount, and on every hashchange
   (Admin's `<svelte:window onhashchange>`). */
export function sync() {
  router.route = parse(location.hash)
}
