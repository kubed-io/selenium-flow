<script lang="ts">
  let { onsubmit, refused }: { onsubmit: (token: string) => void; refused: boolean } = $props()
  let token = $state('')
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
    <p id="loginError" class="small error" hidden={!refused}>That token was refused.</p>
  </div>
</div>
