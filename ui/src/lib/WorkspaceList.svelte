<script lang="ts">
  import { browserMark, metaLine, workspaceLabel } from './format'
  import type { WorkspaceRow } from './types'

  let { data, onpick }: { data: { workspaces?: WorkspaceRow[] } | null; onpick?: (key: string) => void } = $props()
  const workspaces = $derived(data?.workspaces ?? [])
</script>

{#if !workspaces.length}
  <div class="empty">No workspaces yet.</div>
{:else}
  {#each workspaces as s (s.key)}
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions (today's card is mouse-only; changing that is new behaviour) -->
    <div class={['card', onpick && 'click']} onclick={onpick ? () => onpick(s.key) : undefined}>
      <div class="row">
        <span class="bmark" title={s.browser || 'browser'}>{browserMark(s.browser)}</span>
        <span class="pill name">{workspaceLabel(s)}</span>
        <span class="mono grow small muted">{s.session_id || 'no browser'}</span>
        {#if s.live}<span class="pill live">live</span>{:else}<span class="pill">idle</span>{/if}
      </div>
      <div class="small muted" style="margin-top:6px">{metaLine(s)}</div>
      {#if s.url}<div class="small muted url">{s.url}</div>{/if}
    </div>
  {/each}
{/if}
