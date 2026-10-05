<script lang="ts">
  import { plural } from '../format'
  import SecretCard from '../SecretCard.svelte'
  import { warnsOf } from '../secrets'
  import type { Secret, SecretsData } from '../types'

  let { data }: { data: SecretsData } = $props()
  // One secret opens from the card already here rather than another `show`:
  // there is no single-secret resource to read (secrets.py, "one read").
  let open = $state<string | null>(null)
  const chosen = $derived(data.secrets.find((s) => s.name === open))
  const reach = (s: Secret) => (s.restricted ? plural((s.allowed_urls ?? []).length, 'site') : 'any site')
</script>

{#if chosen}
  <div class="back"><button type="button" onclick={() => (open = null)}>← All secrets</button></div>
  <SecretCard secret={chosen} />
{:else if !data.secrets.length}
  <div class="empty">No secrets are configured.</div>
{:else}
  <div class="scroller" role="list">
    {#each data.secrets as s (s.name)}
      <div class="slot" role="listitem">
        <button type="button" class="card pick" aria-label={s.name} onclick={() => (open = s.name)}>
          <div class="row"><span>🔑</span><strong class="grow">{s.name}</strong></div>
          {#if s.description}<div class="desc small muted">{s.description}</div>{/if}
          <div class="small muted">{plural((s.keys ?? []).length, 'key')} · {reach(s)}</div>
          {#if warnsOf(s).length}<div class="warns">{#each warnsOf(s) as warn (warn)}<span class="pill warn">{warn}</span>{/each}</div>{/if}
        </button>
      </div>
    {/each}
  </div>
{/if}

<style>
  .back { margin-bottom: 8px; }
  .back button { padding: 4px 10px; min-height: 32px; }
  .scroller {
    display: flex; gap: var(--gap); overflow-x: auto; overflow-y: hidden;
    scroll-snap-type: x mandatory; scroll-padding-inline: var(--safe-left, 0);
  }
  .slot { flex: 0 0 min(240px, 80%); scroll-snap-align: start; display: flex; }
  .card.pick {
    flex: 1; margin: 0; min-height: 44px; text-align: left; font: inherit; color: var(--ink);
    display: flex; flex-direction: column; gap: 6px; cursor: pointer;
  }
  .card.pick:hover { border-color: var(--accent); }
  .card.pick:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .desc {
    display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  }
  .warns { display: flex; flex-wrap: wrap; gap: 4px; }
</style>
