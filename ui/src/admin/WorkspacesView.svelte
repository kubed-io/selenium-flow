<script lang="ts">
  import WorkspaceList from '../lib/WorkspaceList.svelte'
  import type { Live } from './live.svelte'
  import { go, hashes } from './router.svelte'

  let { live }: { live: Live } = $props()
</script>

<div id="workspacesView">
  <div class="row" style="margin-bottom:12px">
    <strong class="grow">Workspaces</strong>
    {#if live.badge.cls === 'live'}
      <span id="live" class="dot live" role="img" aria-label="Updates arrive as they happen" title="Updates arrive as they happen"></span>
    {:else}
      <span id="live" class={['pill', live.badge.cls]} title="Updates arrive as they happen">{live.badge.text}</span>
    {/if}
  </div>
  <div id="workspaces">
    {#if live.error}
      <div class="empty error">{live.error}</div>
    {:else if !live.data}
      <div class="empty">Loading…</div>
    {:else}
      <WorkspaceList data={live.data} onpick={(key) => go(hashes.workspace(key))} />
    {/if}
  </div>
</div>
