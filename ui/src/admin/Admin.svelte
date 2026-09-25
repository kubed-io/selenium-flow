<script lang="ts">
  import { onMount } from 'svelte'
  import { createApi } from './api'
  import ConsolePane from './ConsolePane.svelte'
  import { Live } from './live.svelte'
  import Login from './Login.svelte'
  import { go, hashes, router, sync } from './router.svelte'
  import SecretsPane from './SecretsPane.svelte'
  import SessionDetail from './SessionDetail.svelte'
  import SessionsView from './SessionsView.svelte'

  let { mount, console: consoleUrl }: { mount: string; console: string } = $props()

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

  // sessionStorage, not localStorage: the server's full-privilege token should
  // not outlive the tab it was typed into. A plain variable: nothing renders
  // it, and the api reads it afresh on every call.
  let token = sessionStorage.getItem('sf-token') || ''
  let phase = $state<'probing' | 'login' | 'in'>(token ? 'probing' : 'login')
  let refused = $state(false)

  const api = createApi({ base: BASE, token: () => token, onUnauthorized: signOut })
  const live = new Live(api, ROOT)

  function signOut() {
    live.stop()
    token = ''
    sessionStorage.removeItem('sf-token')
    phase = 'login'
  }

  async function signIn(value: string) {
    token = value
    try {
      await api('/admin/sessions')
      sessionStorage.setItem('sf-token', token)
      refused = false
      phase = 'in'
    } catch {
      refused = true
    }
  }

  onMount(() => {
    sync()
    if (phase === 'probing') api('/admin/sessions').then(() => { phase = 'in' }, () => { phase = 'login' })
  })

  const route = $derived(router.route)
  const top = $derived(route.view === 'secrets' ? 'secrets' : route.view === 'console' && !consoleSelf ? 'console' : 'sessions')

  // The list reloads whenever it (or the console, which sits on the same live
  // stream) comes on screen; a deep link into a session starts the stream too.
  // `live.watching` must stay the last operand: it's read only for the
  // session branch, so a list or console load never re-triggers this effect
  // by way of the very state its own `live.load()` call goes on to update.
  $effect(() => {
    if (phase !== 'in') return
    if (route.view === 'list' || route.view === 'console' || (route.view === 'session' && !live.watching)) void live.load()
  })
</script>

<svelte:window onhashchange={sync} />
<header class="bar">
  <span class="brand">selenium-flow</span>
  <span class="grow"></span>
  {#if phase === 'in'}<button id="signout" onclick={signOut}>Sign out</button>{/if}
</header>

{#if phase === 'login'}
  <Login onsubmit={signIn} {refused} />
{:else if phase === 'in'}
  <main id="app" class="wrap">
    <div class="tabs" role="tablist">
      <button id="tabSessions" role="tab" aria-selected={top === 'sessions'} onclick={() => go(hashes.list)}>Sessions</button>
      <button id="tabSecrets" role="tab" aria-selected={top === 'secrets'} onclick={() => go(hashes.secrets)}>Secrets</button>
      <button id="tabConsole" role="tab" aria-selected={top === 'console'} hidden={consoleSelf} onclick={() => go(hashes.console)}>Grid console</button>
    </div>
    {#if top === 'sessions'}
      <section id="paneSessions">
        {#if route.view === 'session'}
          <!-- Keyed: a switch destroys the old session's subtree — its loads,
               its lightbox, its modal — and builds the new one from nothing. -->
          {#key route.key}
            <SessionDetail key={route.key} tab={route.tab} flow={route.flow} {api} {live} root={ROOT} />
          {/key}
        {:else}
          <SessionsView {live} />
        {/if}
      </section>
    {:else if top === 'secrets'}
      <SecretsPane {api} />
    {/if}
    {#if !consoleSelf}
      <!-- Mounted once, alongside the other panes, not only on the console
           route: today's page creates this iframe at boot and just toggles
           its section, so switching tabs never reloads the Grid's console. -->
      <ConsolePane src={target.href} hidden={top !== 'console'} />
    {/if}
  </main>
{/if}
