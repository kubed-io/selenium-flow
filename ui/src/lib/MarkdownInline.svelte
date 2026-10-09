<script lang="ts">
  import type { Token, Tokens } from 'marked'
  import { showable, target } from './links'
  import Self from './MarkdownInline.svelte'

  let { tokens, base, onshow, onlink }: {
    tokens: Token[]
    base: string
    onshow?: (uri: string) => void
    onlink?: (url: string) => void
  } = $props()

  function open(e: MouseEvent, url: string) {
    if (!onlink) return
    e.preventDefault()
    onlink(url)
  }
</script>

{#each tokens as t, i (i)}
  {#if t.type === 'text' || t.type === 'escape'}
    {@const x = t as Tokens.Text}
    {#if x.tokens?.length}<Self tokens={x.tokens} {base} {onshow} {onlink} />{:else}{x.text}{/if}
  {:else if t.type === 'strong'}
    <strong><Self tokens={(t as Tokens.Strong).tokens} {base} {onshow} {onlink} /></strong>
  {:else if t.type === 'em'}
    <em><Self tokens={(t as Tokens.Em).tokens} {base} {onshow} {onlink} /></em>
  {:else if t.type === 'del'}
    <del><Self tokens={(t as Tokens.Del).tokens} {base} {onshow} {onlink} /></del>
  {:else if t.type === 'br'}
    <br>
  {:else if t.type === 'codespan'}
    {@const text = (t as Tokens.Codespan).text}
    {@const uri = showable(text)}
    {#if uri && onshow}
      <button type="button" class="md-uri" onclick={() => onshow(uri)}><code>{text}</code></button>
    {:else}
      <code>{text}</code>
    {/if}
  {:else if t.type === 'link'}
    {@const l = t as Tokens.Link}
    {@const to = target(l.href, base)}
    {#if to && to.kind === 'show' && onshow}
      <button type="button" class="md-link" onclick={() => onshow(to.uri)}><Self tokens={l.tokens} {base} {onshow} {onlink} /></button>
    {:else if to && to.kind === 'open'}
      <a href={to.url} target="_blank" rel="noopener noreferrer" onclick={(e) => open(e, to.url)}><Self tokens={l.tokens} {base} {onshow} {onlink} /></a>
    {:else}
      <span><Self tokens={l.tokens} {base} {onshow} {onlink} /></span>
    {/if}
  {:else}
    {t.raw}
  {/if}
{/each}
