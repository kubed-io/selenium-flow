<script lang="ts">
  import { browserMark } from './format'
  import type { WorkspaceRow } from './types'

  /* The workspace card (UI-64): the workspace only. What a session is doing —
     its browser, recording, capture, last page — is on its Session tab. */
  let { data }: { data: WorkspaceRow | null } = $props()
  const s = $derived(data ?? ({ key: '' } as WorkspaceRow))
</script>

<div class="card">
  <div class="row">
    <span class="bmark" title={s.browser || 'browser'}>{browserMark(s.browser)}</span>
    <strong class="grow">{s.name || s.owner || 'Workspace'}</strong>
    {#if s.kept}<span class="small muted">kept · config</span>{/if}
  </div>
</div>
