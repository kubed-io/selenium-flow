/* The hash, not the history API: every other path under this mount is a real
   endpoint, and a history route would need a catch-all that answers a mistyped
   API call with HTML. A hash never reaches the server. */
export type Tab = 'session' | 'files' | 'flows' | 'site-data' | 'history' | 'config' | 'console' | 'network' | 'live'
export type Route =
  | { view: 'list' }
  | { view: 'console' }
  | { view: 'secrets' }
  | { view: 'settings' }
  | { view: 'workspace'; key: string; tab: Tab; flow: string | undefined; site: string | undefined; request: string | undefined; session?: string | undefined }

const TABS: readonly string[] = ['session', 'files', 'flows', 'site-data', 'history', 'config', 'console', 'network', 'live']

export function parse(hash: string): Route {
  // The picked session rides on the address as `?session=`, after the path.
  const [path, query = ''] = hash.split('?', 2)
  const [view, key, tab, rest] = path.replace(/^#\/?/, '').split('/')
  if (view === 'console') return { view: 'console' }
  if (view === 'secrets') return { view: 'secrets' }
  if (view === 'settings') return { view: 'settings' }
  if (view === 'workspaces' && key) {
    // parse() runs at module load: a bad pasted `%` must not stop the app booting.
    try {
      // A workspace opens on Session (spec 2026-10-26-session-capabilities).
      const picked = (TABS.includes(tab) ? tab : 'session') as Tab
      const named = rest ? decodeURIComponent(rest) : undefined
      return {
        view: 'workspace',
        key: decodeURIComponent(key),
        tab: picked,
        // The fourth segment is a flow on Flows and a host on Site data.
        flow: picked === 'flows' ? named : undefined,
        site: picked === 'site-data' ? named : undefined,
        request: picked === 'network' ? named : undefined,
        session: /(?:^|&)session=([^&]*)/.exec(query)?.[1] ? decodeURIComponent(/(?:^|&)session=([^&]*)/.exec(query)![1]) : undefined,
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
  workspace: (key: string) => '#/workspaces/' + enc(key),
  files: (key: string) => '#/workspaces/' + enc(key) + '/files',
  flows: (key: string) => '#/workspaces/' + enc(key) + '/flows',
  siteData: (key: string) => '#/workspaces/' + enc(key) + '/site-data',
  site: (key: string, host: string) => '#/workspaces/' + enc(key) + '/site-data/' + enc(host),
  history: (key: string) => '#/workspaces/' + enc(key) + '/history',
  /* The server's `config_url` is this hash on the admin page. */
  config: (key: string) => '#/workspaces/' + enc(key) + '/config',
  pageConsole: (key: string) => '#/workspaces/' + enc(key) + '/console',
  network: (key: string) => '#/workspaces/' + enc(key) + '/network',
  live: (key: string) => '#/workspaces/' + enc(key) + '/live',
  request: (key: string, id: string) => '#/workspaces/' + enc(key) + '/network/' + enc(id),
  flow: (key: string, name: string) => '#/workspaces/' + enc(key) + '/flows/' + enc(name),
}

export const router = $state<{ route: Route }>({ route: parse(location.hash) })

/* A workspace hash keeps the session picked in the one on screen, unless the
   caller says which (`null`: the default). A hash of another page carries none. */
function carried(hash: string, session?: string | null): string {
  if (session === undefined) {
    const now = router.route
    const here = '#/workspaces/' + enc(now.view === 'workspace' ? now.key : '')
    const same = now.view === 'workspace' && (hash === here || hash.startsWith(here + '/'))
    session = same && !hash.includes('?') ? now.session : undefined
  }
  return session && session !== 'default' ? hash + (hash.includes('?') ? '&' : '?') + 'session=' + enc(session) : hash
}

/* The tab's own hash, whichever it is. */
export const tabHash = (key: string, tab: Tab): string =>
  tab === 'session' ? hashes.workspace(key) : '#/workspaces/' + enc(key) + '/' + tab

export const go = (hash: string, session?: string | null) => { location.hash = carried(hash, session) }

/* In the address bar and the route, without a history entry and without
   hashchange: picking through a dozen flows should not take a dozen Backs. */
export function replace(hash: string, session?: string | null) {
  hash = carried(hash, session)
  history.replaceState(null, '', hash)
  router.route = parse(hash)
}

/* The route, read from the address bar: at mount, and on every hashchange
   (Admin's `<svelte:window onhashchange>`). */
export function sync() {
  router.route = parse(location.hash)
}
