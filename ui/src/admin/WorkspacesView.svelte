<script lang="ts">
  import WorkspaceList from '../lib/WorkspaceList.svelte'
  import type { Live } from './live.svelte'
  import { go, hashes } from './router.svelte'

  let { live }: { live: Live } = $props()
</script>

<div id="workspacesView">
  <div class="row" style="margin-bottom:12px">
    <strong class="grow">Live workspaces</strong>
    <span id="live" class={['pill', live.badge.cls]} title="Updates arrive as they happen">{#if live.badge.cls === 'live'}<span class="dot" aria-hidden="true"></span>{/if}{live.badge.text}</span>
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
