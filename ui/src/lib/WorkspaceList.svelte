<script lang="ts">
  import { browserMark, browserState, metaLine, workspaceLabel } from './format'
  import type { WorkspaceRow } from './types'

  let { data, onpick }: { data: { workspaces?: WorkspaceRow[] } | null; onpick?: (key: string) => void } = $props()
  const workspaces = $derived(data?.workspaces ?? [])
  // The default is the card's own; only the others are listed.
  const subs = (s: WorkspaceRow) => (s.sessions ?? []).filter((x) => !x.default)
</script>

{#if !workspaces.length}
  <div class="empty">No workspaces yet.</div>
{:else}
  {#each workspaces as s (s.key)}
    {@const state = browserState(s)}
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions (today's card is mouse-only; changing that is new behaviour) -->
    <div class={['card', onpick && 'click']} onclick={onpick ? () => onpick(s.key) : undefined}>
      <div class="row">
        <span class="bmark" title={s.browser || 'browser'}>{browserMark(s.browser)}</span>
        <span class="pill name">{workspaceLabel(s)}</span>
        <span class="mono grow small muted">{s.session_id || 'no browser'}</span>
        {#if s.kept}<span class="pill kept">kept</span>{/if}
        {#if state === 'open'}<span class="pill live">open</span>{:else}<span class="pill">{state}</span>{/if}
        {#if s.capture}{#if s.capturing || state === 'unknown'}<span class="pill capture">● capture</span>{:else}<span class="pill capture-paused">capture paused</span>{/if}{/if}
      </div>
      <div class="small muted" style="margin-top:6px">{metaLine(s)}</div>
      {#if s.url}<div class="small muted url">{s.url}</div>{/if}
      {#if subs(s).length}
        <div class="subs small">
          <span class="muted">Sub-sessions</span>
          {#each subs(s) as x (x.name)}
            <span class="sub"><span class={['dot', x.live && 'live']} role="img" aria-label={x.live ? 'live' : 'not live'}></span>{x.label} <span class="muted">{x.browser || 'idle'}</span></span>
          {/each}
        </div>
      {/if}
    </div>
  {/each}
{/if}

<style>
  .subs { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 8px; }
  .sub { display: inline-flex; align-items: center; gap: 6px; padding: 2px 10px; border: 1px solid var(--line); border-radius: 999px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; border: 1.5px solid var(--muted); box-sizing: border-box; }
  .dot.live { background: var(--ok); border-color: var(--ok); }
</style>
