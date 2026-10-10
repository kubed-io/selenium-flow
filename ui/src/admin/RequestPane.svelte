<script lang="ts">
  import { onMount } from 'svelte'
  import { clock, isFailed, kb, statusText } from '../lib/capture'
  import type { BodyResult, Header, RequestDetail } from '../lib/types'
  import { workspacePath, type Api } from './api'
  import { go, hashes } from './router.svelte'

  let { api, key, id }: { api: Api; key: string; id: string } = $props()
  let d = $state.raw<RequestDetail | null>(null)
  let error = $state<string | null>(null)
  let body = $state.raw<BodyResult | null>(null)
  let fetched = $state('')
  const base = $derived(workspacePath(key, '/network/' + encodeURIComponent(id)))

  onMount(() => {
    api<RequestDetail>(base).then((r) => (d = r), (e: Error) => (error = e.message))
  })

  async function fetchBody() {
    error = null
    try {
      body = await api<BodyResult>(base + '/body', 'POST')
      fetched = clock(Date.now() / 1000)
    } catch (e) {
      body = null
      error = (e as Error).message
    }
  }
  const pairs = (r: RequestDetail): [string, [string, string][], string?][] => {
    const masked = r.request_headers.filter((h) => h.value === '•••').length
    const h = (hs: Header[]): [string, string][] => hs.map((x) => [x.name, x.value])
    return [
      ['GENERAL', [
        ['status', r.failed ? (r.error ?? 'failed') : `${r.status ?? ''} ${r.status_text ?? ''}`.trim()],
        ['type', r.type],
        ['started', clock(r.at) + '.' + String(Math.floor((r.at % 1) * 1000)).padStart(3, '0')],
        ['duration', (r.took_ms ?? '—') + ' ms'],
        ['size', kb(r.size)],
      ]],
      ['REQUEST HEADERS', h(r.request_headers), masked ? masked + ' headers masked' : ''],
      ['RESPONSE HEADERS', h(r.response_headers)],
    ]
  }
</script>

<div class="row">
  <button onclick={() => go(hashes.network(key))}>← Requests</button>
  {#if d}<strong class="grow">{d.method} {d.url}</strong><span class="pill" class:warn={isFailed(d)}>{statusText(d)}</span>{/if}
</div>
{#if d}
  <div class="rcard">
    {#each pairs(d) as [title, rows, note] (title)}
      <div class="block">
        <span class="label">{title}</span>
        {#each rows as [k, v], i (i)}<div class="kv"><span>{k}</span><span>{v}</span></div>{/each}
        {#if note}<span class="muted">{note}</span>{/if}
      </div>
    {/each}
    <div class="block">
      <span class="label">RESPONSE BODY</span>
      <div class="row"><button onclick={fetchBody}>Fetch body</button>
        <span class="muted">read from the browser on demand · kept while the browser lives · up to 100 kB</span></div>
      {#if body?.binary}
        <span class="muted">{body.mime} · {kb(body.size)} · not shown</span>
      {:else if body}
        <pre class:warn={isFailed(d)}>{body.text}</pre>
        <span class="muted">{body.size} bytes · {body.truncated ? 'truncated at 100 kB' : 'complete'} · fetched {fetched}</span>
      {/if}
    </div>
  </div>
  <p class="small muted">workspace://network/{id}</p>
{/if}
{#if error}<p class="small warn">{error}</p>{/if}
