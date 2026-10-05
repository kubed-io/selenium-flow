<script lang="ts">
  import type { FlowsData } from '../types'

  let { data, onshow }: { data: FlowsData; onshow?: (uri: string) => void } = $props()
  const uri = (name: string) => 'flow://flows/' + encodeURIComponent(name)
  const params = (p?: { properties?: object }) => Object.keys(p?.properties ?? {}).length
</script>

{#if !data.flows.length}
  <div class="empty">No flows yet.</div>
{:else}
  <div class="scroller" role="list">
    {#each data.flows as f (f.name)}
      {#snippet body()}
        <div class="row">
          <strong class="grow">{f.name}</strong>
          {#if f.shared}<span class="pill" title="shared">🌐</span>{/if}
        </div>
        {#if f.description}<div class="desc small muted">{f.description}</div>{/if}
        <div class="small muted">{params(f.parameters)} params · {f.step_count} steps</div>
      {/snippet}
      <div class="slot" role="listitem">
        {#if onshow}
          <button type="button" class="card flow" aria-label={f.name} onclick={() => onshow(uri(f.name))}>{@render body()}</button>
        {:else}
          <div class="card flow">{@render body()}</div>
        {/if}
      </div>
    {/each}
  </div>
{/if}

<style>
  .scroller {
    display: flex; gap: var(--gap); overflow-x: auto; overflow-y: hidden;
    scroll-snap-type: x mandatory; scroll-padding-inline: var(--safe-left, 0);
  }
  .slot { flex: 0 0 min(240px, 80%); scroll-snap-align: start; display: flex; }
  .card.flow {
    flex: 1; margin: 0; min-height: 44px; text-align: left; font: inherit; color: var(--ink);
    display: flex; flex-direction: column; gap: 6px;
  }
  button.card.flow { cursor: pointer; }
  button.card.flow:hover { border-color: var(--accent); }
  button.card.flow:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .desc {
    display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  }
</style>
