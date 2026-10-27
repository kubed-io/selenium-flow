<script lang="ts">
  import { browserMark, safeHref } from './format'
  import type { WorkspaceRow } from './types'

  /* The masthead: which workspace, how it is, and where it was. The facts
     about it and its browser live on the Session tab (spec
     2026-10-26-session-capabilities); the pills stay here as they were. */
  let { data }: { data: WorkspaceRow | null } = $props()
  const s = $derived(data ?? ({ key: '' } as WorkspaceRow))
  const href = $derived(safeHref(s.url))
</script>

<div class="card">
  <div class="row" style="margin-bottom:10px">
    <span class="bmark" title={s.browser || 'browser'}>{browserMark(s.browser)}</span>
    <strong class="grow">{s.name || s.owner || 'Workspace'}</strong>
    {#if s.live}<span class="pill live">live</span>{:else}<span class="pill">idle</span>{/if}
    {#if s.live && s.recording}<span class="pill rec">● REC</span>{/if}
    {#if s.capture}{#if s.capturing}<span class="pill capture">● capture</span>{:else}<span class="pill capture-paused">capture paused</span>{/if}{/if}
  </div>
  <div class="lastpage masthead">
    <div class="k small muted">last page</div>
    {#if href}
      <a {href} target="_blank" rel="noopener noreferrer">{s.url}</a>
    {:else}
      <span class="small muted">{s.url || 'nowhere yet'}</span>
    {/if}
  </div>
</div>

<style>
  /* Nothing sits below it any more, so no rule to close it off. */
  .masthead { border-bottom: none; margin-bottom: 0; padding-bottom: 0; }
</style>
