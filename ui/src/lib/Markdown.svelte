<script lang="ts">
  import type { Token, Tokens } from 'marked'
  import Inline from './MarkdownInline.svelte'
  import Self from './Markdown.svelte'

  let { tokens, base, onshow, onlink }: {
    tokens: Token[]
    base: string
    onshow?: (uri: string) => void
    onlink?: (url: string) => void
  } = $props()

  // The document's # is its title; what is left starts at h2.
  const tag = (depth: number) => 'h' + Math.min(6, Math.max(2, depth))
</script>

{#each tokens as t, i (i)}
  {#if t.type === 'heading'}
    {@const h = t as Tokens.Heading}
    <svelte:element this={tag(h.depth)}><Inline tokens={h.tokens} {base} {onshow} {onlink} /></svelte:element>
  {:else if t.type === 'paragraph'}
    <p><Inline tokens={(t as Tokens.Paragraph).tokens} {base} {onshow} {onlink} /></p>
  {:else if t.type === 'text'}
    {@const x = t as Tokens.Text}
    {#if x.tokens?.length}<Inline tokens={x.tokens} {base} {onshow} {onlink} />{:else}{x.text}{/if}
  {:else if t.type === 'list'}
    {@const l = t as Tokens.List}
    {#if l.ordered}
      <ol start={typeof l.start === 'number' ? l.start : undefined}>
        {#each l.items as item, j (j)}<li><Self tokens={item.tokens} {base} {onshow} {onlink} /></li>{/each}
      </ol>
    {:else}
      <ul>
        {#each l.items as item, j (j)}<li><Self tokens={item.tokens} {base} {onshow} {onlink} /></li>{/each}
      </ul>
    {/if}
  {:else if t.type === 'code'}
    <pre><code>{(t as Tokens.Code).text}</code></pre>
  {:else if t.type === 'table'}
    {@const tb = t as Tokens.Table}
    <div class="md-table">
      <table>
        <thead><tr>{#each tb.header as c, j (j)}<th><Inline tokens={c.tokens} {base} {onshow} {onlink} /></th>{/each}</tr></thead>
        <tbody>
          {#each tb.rows as row, j (j)}
            <tr>{#each row as c, k (k)}<td><Inline tokens={c.tokens} {base} {onshow} {onlink} /></td>{/each}</tr>
          {/each}
        </tbody>
      </table>
    </div>
  {:else if t.type === 'blockquote'}
    <blockquote><Self tokens={(t as Tokens.Blockquote).tokens} {base} {onshow} {onlink} /></blockquote>
  {:else if t.type === 'hr'}
    <hr>
  {:else if t.type === 'space'}
    <!-- nothing to draw -->
  {:else}
    <p>{t.raw}</p>
  {/if}
{/each}
