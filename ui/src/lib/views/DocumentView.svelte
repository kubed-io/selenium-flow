<script lang="ts">
  import { isRecord, leaf, readable } from '../format'
  import JsonTree from '../JsonTree.svelte'
  import Markdown from '../Markdown.svelte'
  import MarkdownInline from '../MarkdownInline.svelte'
  import { parse } from '../markdown'

  let { data, uri = '', onshow, onlink, expanded = false, expandable = false }: {
    data: unknown
    uri?: string
    onshow?: (uri: string) => void
    onlink?: (url: string) => void
    expanded?: boolean
    expandable?: boolean
  } = $props()

  // Inline, a document stops here when fullscreen can show the rest (ruling 6).
  const LIMIT = 6
  const doc = $derived(typeof data === 'string' ? parse(data) : null)
  const blocks = $derived(doc ? (expanded || !expandable ? doc.blocks : doc.blocks.slice(0, LIMIT)) : [])
  const more = $derived(doc ? doc.blocks.length - blocks.length : 0)
  const jsonTitle = $derived(isRecord(data) && typeof data.title === 'string' ? data.title : leaf(uri))
</script>

<div class="card doc">
  <div class="row head">
    <strong>
      {#if doc?.title}<MarkdownInline tokens={doc.title.tokens} base={uri} />{:else if doc}{leaf(uri)}{:else}{jsonTitle}{/if}
    </strong>
    <code class="uri small muted grow">{readable(uri)}</code>
  </div>
  {#if doc}
    <div class="md"><Markdown tokens={blocks} base={uri} {onshow} {onlink} /></div>
    {#if more > 0}<div class="small muted">+{more} more</div>{/if}
  {:else}
    <JsonTree value={data} />
  {/if}
</div>

<style>
  .head { margin-bottom: 10px; flex-wrap: wrap; }
</style>
