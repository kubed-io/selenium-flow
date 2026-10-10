<script lang="ts">
  import { clock, isError } from '../lib/capture'
  import type { ConsoleEntry } from '../lib/types'
  import type { Api } from './api'
  import CaptureList from './CaptureList.svelte'

  let { api, key, root }: { api: Api; key: string; root: string } = $props()
  const cells = (e: ConsoleEntry): [string, string][] => [
    [e.level, isError(e) ? 'pill warn' : 'pill'],
    [e.text, isError(e) ? 'text mono warn' : 'text mono'],
    [e.source ? (e.source.url.split('/').pop() || e.source.url) + ':' + e.source.line : '', 'mono muted'],
    [clock(e.at), 'muted'],
  ]
</script>

<CaptureList {api} {key} {root} kind="console" {cells} empty="No messages since the last navigation"
  cleared="Entries that arrive from now on show here. The browser keeps running; nothing was sent to it." />
