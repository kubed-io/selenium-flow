<script lang="ts">
  import { onDestroy, onMount } from 'svelte'
  import { ApiError, createApi } from './api'
  import ConsolePane from './ConsolePane.svelte'
  import { Live } from './live.svelte'
  import Login from './Login.svelte'
  import { begin, complete, hasReply, MARKER, NOT_REACHED, readConfig, refresh, renewIn, usernameOf, type Tokens } from './oidc'
  import { go, hashes, router, sync } from './router.svelte'
  import SecretsPane from './SecretsPane.svelte'
  import WorkspaceDetail from './WorkspaceDetail.svelte'
  import WorkspacesView from './WorkspacesView.svelte'
  import SettingsPane from './SettingsPane.svelte'

  let { mount, console: consoleUrl, oidc: oidcRaw = '' }: { mount: string; console: string; oidc?: string } = $props()

  // Served at the root of wherever this server is mounted, so its own path is
  // the base every call hangs off. Server-issued URLs already carry the mount
  // and resolve against ROOT: whatever an ingress stripped. Plain consts, not
  // $derived: `mount` and `consoleUrl` are set once when this app boots from
  // the host page's data attributes and never change for the life of the tab.
  const BASE = location.pathname.replace(/\/+$/, '')
  // svelte-ignore state_referenced_locally (mount is a boot-time prop, read once by design)
  const ROOT = mount && BASE.endsWith(mount) ? BASE.slice(0, -mount.length) : BASE

  // At a root mount the default console URL is this page: framing it nests the
  // dashboard in itself without end. No tab beats a mirror.
  // svelte-ignore state_referenced_locally (consoleUrl is a boot-time prop, read once by design)
  const target = new URL(consoleUrl, location.href)
  const consoleSelf = target.origin === location.origin && target.pathname.replace(/\/+$/, '') === BASE

  // svelte-ignore state_referenced_locally (oidcRaw is a boot-time prop, read once by design)
  const oidc = readConfig(oidcRaw)
  const framed = window !== window.top

  // sessionStorage, not localStorage: the server's full-privilege token should
  // not outlive the tab it was typed into. A plain variable: nothing renders
  // it, and the api reads it afresh on every call.
  let token = sessionStorage.getItem('sf-token') || ''
  // An OIDC sign-in's tokens: memory only, so a reload signs in again —
  // silently while the issuer's session lives (spec 2026-10-09-admin-oidc).
  let tokens: Tokens | null = null
  let renewal: ReturnType<typeof setTimeout> | undefined
  const resuming = !!oidc && (hasReply() || !!sessionStorage.getItem(MARKER))
  let phase = $state<'probing' | 'login' | 'in'>(token || resuming ? 'probing' : 'login')
  let refused = $state(false)
  let said = $state<string | null>(null)

  const api = createApi({ base: BASE, token: () => tokens?.access ?? token, onUnauthorized: () => signOut() })
  const live = new Live(api, ROOT)

  function signOut(message: string | null = null) {
    live.stop()
    clearTimeout(renewal)
    token = ''
    tokens = null
    sessionStorage.removeItem('sf-token')
    sessionStorage.removeItem(MARKER)
    said = message
    phase = 'login'
  }

  async function signIn(value: string) {
    token = value
    said = null
    try {
      await api('/admin/workspaces')
      sessionStorage.setItem('sf-token', token)
      refused = false
      phase = 'in'
    } catch {
      refused = true
    }
  }

  async function startOidc(silent: boolean) {
    if (!oidc) return
    try {
      await begin(oidc, silent)
    } catch {
      said = NOT_REACHED
      phase = 'login'
    }
  }

  async function finishOidc() {
    if (!oidc) return
    const reply = await complete(oidc)
    if (reply.kind !== 'tokens') {
      said = reply.kind === 'error' ? reply.message : null
      phase = 'login'
      return
    }
    tokens = reply.tokens
    try {
      await api('/admin/workspaces')
      sessionStorage.setItem(MARKER, 'oidc')
      schedule()
      refused = false
      phase = 'in'
    } catch (e) {
      const who = usernameOf(reply.tokens.access) ?? 'this account'
      signOut(e instanceof ApiError && e.status === 403
        ? `Signed in as ${who}, who does not hold an admin role.`
        : 'The server refused this sign-in.')
    }
  }

  // Renew 30 s before the access token runs out; a failed renewal ends it.
  function schedule() {
    clearTimeout(renewal)
    if (!oidc || !tokens?.refresh) return
    renewal = setTimeout(async () => {
      const current = tokens
      if (!current) return
      try {
        const next = await refresh(oidc, current)
        // Signed out (or unmounted) while the issuer answered: keep nothing.
        if (tokens !== current) return
        tokens = next
        schedule()
      } catch {
        if (tokens === current) signOut('Your sign-in ended; sign in again.')
      }
    }, renewIn(tokens))
  }

  onMount(() => {
    sync()
    // A reply first: complete() takes the code out of the address bar before
    // anything else runs.
    if (oidc && hasReply()) { void finishOidc(); return }
    if (token) { api('/admin/workspaces').then(() => { phase = 'in' }, () => { phase = 'login' }); return }
    if (oidc && sessionStorage.getItem(MARKER)) {
      // Removed before leaving: an issuer that is down must not loop the page.
      sessionStorage.removeItem(MARKER)
      void startOidc(true)
    }
  })
  onDestroy(() => { clearTimeout(renewal); tokens = null })

  const route = $derived(router.route)
  const top = $derived(route.view === 'secrets' ? 'secrets' : route.view === 'settings' ? 'settings' : route.view === 'console' && !consoleSelf ? 'console' : 'workspaces')

  // The list reloads whenever it (or the console, which sits on the same live
  // stream) comes on screen; a deep link into a workspace starts the stream too.
  // `live.watching` must stay the last operand: it's read only for the
  // workspace branch, so a list or console load never re-triggers this effect
  // by way of the very state its own `live.load()` call goes on to update.
  $effect(() => {
    if (phase !== 'in') return
    if (route.view === 'list' || route.view === 'console' || (route.view === 'workspace' && !live.watching)) void live.load()
  })
</script>

<svelte:window onhashchange={sync} />
<header class="bar">
  <span class="brand">selenium-flow</span>
  <span class="grow"></span>
  {#if phase === 'in'}<button id="signout" onclick={() => signOut()}>Sign out</button>{/if}
</header>

{#if phase === 'login'}
  <Login onsubmit={signIn} {refused} {said} oidc={oidc ? { framed, start: () => void startOidc(false) } : null} />
{:else if phase === 'in'}
  <main id="app" class="wrap">
    <div class="tabs" role="tablist">
      <button id="tabWorkspaces" role="tab" aria-selected={top === 'workspaces'} onclick={() => go(hashes.list)}>Workspaces</button>
      <button id="tabSecrets" role="tab" aria-selected={top === 'secrets'} onclick={() => go(hashes.secrets)}>Secrets</button>
      <button id="tabSettings" role="tab" aria-selected={top === 'settings'} onclick={() => go(hashes.settings)}>Settings</button>
      <button id="tabConsole" role="tab" aria-selected={top === 'console'} hidden={consoleSelf} onclick={() => go(hashes.console)}>Grid console</button>
    </div>
    {#if top === 'workspaces'}
      <section id="paneWorkspaces">
        {#if route.view === 'workspace'}
          <!-- Keyed: a switch destroys the old workspace's subtree — its loads,
               its lightbox, its modal — and builds the new one from nothing. -->
          {#key route.key}
            <WorkspaceDetail key={route.key} tab={route.tab} flow={route.flow} site={route.site} {api} {live} root={ROOT} />
          {/key}
        {:else}
          <WorkspacesView {live} />
        {/if}
      </section>
    {:else if top === 'secrets'}
      <SecretsPane {api} />
    {:else if top === 'settings'}
      <SettingsPane {api} />
    {/if}
    {#if !consoleSelf}
      <!-- Mounted once, alongside the other panes, not only on the console
           route: today's page creates this iframe at boot and just toggles
           its section, so switching tabs never reloads the Grid's console. -->
      <ConsolePane src={target.href} hidden={top !== 'console'} />
    {/if}
  </main>
{/if}
