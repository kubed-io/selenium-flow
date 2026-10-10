<script lang="ts">
  import { clock, isFailed, kb, statusText } from '../lib/capture'
  import type { RequestRow } from '../lib/types'
  import type { Api } from './api'
  import CaptureList from './CaptureList.svelte'
  import { go, hashes } from './router.svelte'

  let { api, key, root }: { api: Api; key: string; root: string } = $props()
  const bad = isFailed
  const cells = (r: RequestRow): [string, string][] => [
    [r.method, 'mono b'],
    [statusText(r), bad(r) ? 'pill warn' : (r.status ?? 0) >= 200 && (r.status ?? 0) < 300 ? 'pill ok' : 'pill'],
    [r.url, bad(r) ? 'text warn' : 'text'],
    [r.type, 'muted'],
    [r.failed ? (r.error ?? '') : kb(r.size), r.failed ? 'mono warn' : 'mono muted'],
    [(r.took_ms ?? '—') + ' ms', 'mono muted'],
    [clock(r.at), 'mono muted'],
  ]
</script>

<CaptureList {api} {key} {root} kind="network" {cells} open={(r: RequestRow) => go(hashes.request(key, r.id))} empty="No requests since the last navigation"
  cleared="Requests from now on show here. Bodies the browser still holds stay fetchable from their request." />
