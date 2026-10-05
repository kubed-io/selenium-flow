<script lang="ts">
  import { onMount } from 'svelte'
  import SecretCard from '../lib/SecretCard.svelte'
  import type { SecretsPayload } from '../lib/types'
  import type { Api } from './api'
  import { Latest } from './latest'

  let { api }: { api: Api } = $props()
  // Raw: an API response, replaced wholesale, never mutated.
  let data = $state.raw<SecretsPayload | null>(null)
  let error = $state<string | null>(null)
  const loads = new Latest()

  onMount(() => {
    void loads.run((signal) => api<SecretsPayload>('/admin/secrets', 'GET', undefined, signal),
      (d) => { data = d; error = null }, (e) => { error = e.message })
    return () => loads.abort()
  })
</script>

<section id="paneSecrets">
  <div id="secrets">
    {#if error}
      <div class="empty error">{error}</div>
    {:else if !data}
      <div class="empty">Loading…</div>
    {:else if !data.enabled}
      <div class="empty">No secrets are configured on this server.</div>
    {:else}
      <h2>Secrets</h2>
      <p class="small muted">What flows can type without anyone seeing it. Read-only here: names, keys and where each may be used — never a value.</p>
      {#each data.secrets as s (s.name)}<SecretCard secret={s} />{/each}
    {/if}
  </div>
</section>
