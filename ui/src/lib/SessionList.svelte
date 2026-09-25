<script lang="ts">
  import { browserMark, metaLine, sessionLabel } from './format'
  import type { SessionRow } from './types'

  let { data, onpick }: { data: { sessions?: SessionRow[] } | null; onpick?: (key: string) => void } = $props()
  const sessions = $derived(data?.sessions ?? [])
</script>

{#if !sessions.length}
  <div class="empty">No sessions yet.</div>
{:else}
  {#each sessions as s (s.key)}
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions (today's card is mouse-only; changing that is new behaviour) -->
    <div class="card" class:click={!!onpick} onclick={onpick ? () => onpick(s.key) : undefined}>
      <div class="row">
        <span class="bmark" title={s.browser || 'browser'}>{browserMark(s.browser)}</span>
        <span class="pill name">{sessionLabel(s)}</span>
        <span class="mono grow small muted">{s.session_id || 'no browser'}</span>
        {#if s.live}<span class="pill live">live</span>{:else}<span class="pill">idle</span>{/if}
      </div>
      <div class="small muted" style="margin-top:6px">{metaLine(s)}</div>
      {#if s.url}<div class="small muted url">{s.url}</div>{/if}
    </div>
  {/each}
{/if}
