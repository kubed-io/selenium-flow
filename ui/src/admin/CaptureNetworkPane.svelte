<script lang="ts">
  import { clock, isFailed, kb, statusText } from '../lib/capture'
  import type { RequestRow } from '../lib/types'
  import type { Api } from './api'
  import CaptureList from './CaptureList.svelte'
  import { go, hashes } from './router.svelte'

  let { api, key, root }: { api: Api; key: string; root: string } = $props()
  const cells = (r: RequestRow): [string, string][] => [
    [r.method, 'mono'], [statusText(r), isFailed(r) ? 'pill warn' : 'pill'], [r.url, 'text'], [r.type, 'small muted'],
    [r.failed ? (r.error ?? '') : kb(r.size), r.failed ? 'small warn' : 'small'], [(r.took_ms ?? '—') + ' ms', 'small muted'], [clock(r.at), 'small muted'],
  ]
</script>

<CaptureList {api} {key} {root} kind="network" {cells} open={(r: RequestRow) => go(hashes.request(key, r.id))} empty="No requests since the last navigation"
  cleared="Requests from now on show here. Bodies the browser still holds stay fetchable from their request." />
