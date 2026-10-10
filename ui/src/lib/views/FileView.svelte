<script lang="ts">
  import { bytes, clock, duration, glyphFor, readable, safeHref } from '../format'
  import type { FileEntry } from '../types'

  let { data, uri = '', onlink }: {
    data: FileEntry
    uri?: string
    onshow?: (uri: string) => void
    onlink?: (url: string) => void
  } = $props()

  const href = $derived(safeHref(data.url))
  const kind = $derived(data.content_type ?? '')
  const video = $derived(kind.startsWith('video/'))
  const pdf = $derived(kind === 'application/pdf')
  const address = $derived(data.uri ?? uri)
  const recording = $derived(address.startsWith('workspace://files/recordings/'))
  // A signed link that has expired, or a host that cannot reach this server.
  let broken = $state(false)
  let length = $state<number | null>(null)
  const facts = $derived(
    [
      length === null ? '' : duration(length),
      bytes(data.size),
      data.created ? clock(data.created) : '',
      data.image || video ? '' : kind,
    ].filter(Boolean).join(' · '),
  )

  function open(e: MouseEvent) {
    // Without the host's openLinks the plain link is the way out.
    if (!onlink) return
    e.preventDefault()
    onlink(href)
  }
</script>

<div class="card fileview">
  <div class="row head">
    <strong class="name">{data.name}</strong>
    <code class="uri small muted grow">{readable(address)}</code>
    {#if recording}<span class="pill rec">● REC</span>{/if}
  </div>
  <div class="preview">
    {#if broken || !href}
      <p class="small error">This link has expired, or this server's address is not reachable from here (PUBLIC_BASE_URL).</p>
    {:else if data.image}
      <img alt={data.name} src={href} onerror={() => (broken = true)}>
    {:else if video}
      <!-- svelte-ignore a11y_media_has_caption -->
      <video controls preload="metadata" src={href}
             onerror={() => (broken = true)}
             onloadedmetadata={(e) => (length = (e.currentTarget as HTMLVideoElement).duration)}></video>
    {:else if pdf}
      <iframe title={data.name} src={href}></iframe>
    {:else}
      <span class="glyph">{glyphFor(data.name)}</span>
    {/if}
  </div>
  <div class="row foot">
    <span class="small muted grow">{facts}</span>
    {#if href}<a class="btn" {href} target="_blank" rel="noopener" onclick={open}>Open</a>{/if}
  </div>
</div>

<style>
  .head { margin-bottom: 10px; flex-wrap: wrap; }
  .name { overflow-wrap: anywhere; }
  .preview { border-radius: var(--radius); overflow: hidden; background: color-mix(in srgb, var(--ink) 6%, transparent); }
  .preview img { display: block; width: 100%; max-height: 480px; object-fit: contain; }
  .preview video { display: block; width: 100%; aspect-ratio: 16 / 9; background: #000; }
  .preview iframe { display: block; width: 100%; height: 420px; border: 0; }
  .preview .glyph { display: grid; place-items: center; height: 120px; font-size: 48px; }
  .preview .error { padding: 16px; margin: 0; }
  .foot { margin-top: 10px; }
</style>
