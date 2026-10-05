<script lang="ts">
  import { fromOf, keyLabel, reasonOf, warnsOf } from './secrets'
  import type { Secret } from './types'

  let { secret: s }: { secret: Secret } = $props()
  const warns = $derived(warnsOf(s))
  const reason = $derived(reasonOf(s))
</script>

<div class="card secret">
  <div class="row"><span>🔑</span><strong class="grow">{s.name}</strong>{#each warns as warn (warn)}<span class="pill warn">{warn}</span>{/each}</div>
  {#if s.description}<div>{s.description}</div>{/if}
  {#if reason}<div class="small error">{reason}</div>{/if}
  {#if s.keys}
    <div class="fact"><span class="k">keys</span>{#each s.keys as k (k)}<span class="pill">{keyLabel(s, k)}</span>{/each}</div>
    <div class="fact"><span class="k">allowed</span>{s.restricted ? (s.allowed_urls || []).join(', ') || '—' : 'any site'}</div>
  {/if}
  {#if s.origins?.length}<div class="fact"><span class="k">from</span><span class="small muted">{fromOf(s)}</span></div>{/if}
</div>
