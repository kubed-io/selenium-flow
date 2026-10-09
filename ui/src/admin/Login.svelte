<script lang="ts">
  let { onsubmit, refused, said = null, oidc = null }: {
    onsubmit: (token: string) => void
    refused: boolean
    said?: string | null
    oidc?: { framed: boolean; start: () => void } | null
  } = $props()
  let token = $state('')

  // In a frame the issuer's login page will not load, so the button opens this
  // page in a tab of its own (spec 2026-10-09-admin-oidc, ruling 10).
  function startOidc() {
    if (!oidc) return
    if (oidc.framed) window.open(location.href, '_blank', 'noopener')
    else oidc.start()
  }
</script>

<div id="login" class="wrap login">
  <div class="card">
    <h2 style="margin-top:0">Sign in</h2>
    <p class="muted small">
      There are no accounts here. The server's token is the whole credential —
      anyone holding it can already drive every browser through the API, so this
      box asks for that rather than inventing a second identity to get wrong.
    </p>
    <form id="loginForm" class="row" onsubmit={(e) => { e.preventDefault(); onsubmit(token.trim()) }}>
      <input id="token" type="password" placeholder="MCP token" autocomplete="off" required bind:value={token}>
      <button class="primary" type="submit">Enter</button>
    </form>
    {#if oidc}
      <!-- Below the token, as drawn (Penpot Admin UI → Admin → login, `oidc`):
           the token never goes away. -->
      <div id="oidcRow" class="row oidc">
        <span class="small muted">or</span>
        <button id="oidcSignIn" type="button" onclick={startOidc}>Sign in with OIDC</button>
        <span class="small muted">{oidc.framed ? 'opens in a new tab: the issuer will not load in a frame' : 'the configured issuer · needs the admin role'}</span>
      </div>
    {/if}
    <p id="loginError" class="small error" hidden={!refused && !said}>{said ?? 'That token was refused.'}</p>
  </div>
</div>

<style>
  .oidc { margin-top: 12px; }
</style>
