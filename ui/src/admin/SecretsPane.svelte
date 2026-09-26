<script lang="ts">
  import { onMount } from 'svelte'
  import type { Secret, SecretsPayload, SecretUse } from '../lib/types'
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

  const flowHash = (u: SecretUse) => '#/sessions/' + encodeURIComponent(String(u.session)) + '/flows/' + encodeURIComponent(u.flow)
  const keyLabel = (s: Secret, k: string) => {
    const from = s.key_sources?.[k]
    if (!from || from.from === 'filesystem') return k
    if (from.from === 'env') return `${k} · env ${from.name}`
    return `${k} · ${from.from}`
  }
  // A list, not one pill: an unrestricted secret with an inline or unresolved
  // key needs BOTH warnings shown, not whichever one happened to be checked
  // first. Order matches severity. `any site` is withheld when the leash
  // itself is broken — that secret is restricted, just unusably so.
  const warnsOf = (s: Secret) => [
    s.allowed_urls_rejected ? 'unusable until fixed' : null,
    s.keys_unresolved?.length ? 'key unresolved' : null,
    s.inline_keys?.length ? 'inline value' : null,
    !s.restricted && !s.allowed_urls_rejected ? 'any site' : null,
  ].filter((w): w is string => w !== null)
  const reasonOf = (s: Secret) =>
    s.allowed_urls_rejected ? 'allowed_urls: ' + ([] as string[]).concat(s.allowed_urls_rejected).join(', ')
      : s.keys_unresolved?.length ? s.keys_unresolved.map((u) => `${u.key}: ${u.reason}`).join('; ')
      : ''
  const fromOf = (s: Secret) => (s.origins ?? []).map((o) => `${o.source} · ${o.location}`).join(' + ')
</script>

{#snippet card(s: Secret, warns: string[], reason: string)}
  <div class="card secret">
    <div class="row"><span>🔑</span><strong class="grow">{s.name}</strong>{#each warns as warn (warn)}<span class="pill warn">{warn}</span>{/each}</div>
    {#if s.description}<div>{s.description}</div>{/if}
    {#if reason}<div class="small error">{reason}</div>{/if}
    {#if s.keys}
      <div class="fact"><span class="k">keys</span>{#each s.keys as k (k)}<span class="pill">{keyLabel(s, k)}</span>{/each}</div>
      <div class="fact"><span class="k">allowed</span>{s.restricted ? (s.allowed_urls || []).join(', ') || '—' : 'any site'}</div>
    {/if}
    {#if s.origins?.length}<div class="fact"><span class="k">from</span><span class="small muted">{fromOf(s)}</span></div>{/if}
    <div class="used">
      <div class="k">USED BY</div>
      {#each s.uses || [] as u (`${u.session ?? ''}/${u.flow}`)}
        <div class="use">
          <span class="pill name">{u.flow}</span>
          <span class="small muted">step{u.steps.length === 1 ? ' ' : 's '}{u.steps.join(', ')}</span>
          <span class="grow"></span>
          {#if u.shared}<span class="small muted">🌐 shared</span>{:else}<a href={flowHash(u)}>{u.session} →</a>{/if}
        </div>
      {:else}
        <div class="small muted">No flow uses it.</div>
      {/each}
    </div>
  </div>
{/snippet}

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
      {#each data.secrets as s (s.name)}{@render card(s, warnsOf(s), reasonOf(s))}{/each}
      {#if data.undefined.length}
        <h3>Named by a flow, not defined</h3>
        <p class="small muted">These flows will fail at the step that types the secret.</p>
        {#each data.undefined as u (u.name)}
          {@render card({ name: u.name, uses: u.uses }, ['not defined'], 'A flow names this secret and the catalogue has no such entry, so the step will fail when it runs.')}
        {/each}
      {/if}
    {/if}
  </div>
</section>
