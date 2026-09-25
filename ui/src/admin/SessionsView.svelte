<script lang="ts">
  import SessionList from '../lib/SessionList.svelte'
  import type { Live } from './live.svelte'
  import { go, hashes } from './router.svelte'

  let { live }: { live: Live } = $props()
</script>

<div id="sessionsView">
  <div class="row" style="margin-bottom:12px">
    <strong class="grow">Live sessions</strong>
    <span id="live" class={['pill', live.badge.cls]} title="Updates arrive as they happen">{#if live.badge.cls === 'live'}<span class="dot" aria-hidden="true"></span>{/if}{live.badge.text}</span>
  </div>
  <div id="sessions">
    {#if live.error}
      <div class="empty error">{live.error}</div>
    {:else if !live.data}
      <div class="empty">Loading…</div>
    {:else}
      <SessionList data={live.data} onpick={(key) => go(hashes.session(key))} />
    {/if}
  </div>
</div>
