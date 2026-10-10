<script lang="ts">
  import Self from './JsonTree.svelte'

  let { value, name = null, depth = 0 }: { value: unknown; name?: string | null; depth?: number } = $props()

  const branch = $derived(value !== null && typeof value === 'object')
  const entries = $derived<[string, unknown][]>(
    !branch ? [] : Array.isArray(value) ? value.map((v, i) => [String(i), v]) : Object.entries(value as Record<string, unknown>),
  )
  const count = $derived(Array.isArray(value) ? '[' + entries.length + ']' : '{' + entries.length + '}')
  const scalar = (v: unknown) => (typeof v === 'string' ? JSON.stringify(v) : String(v))
</script>

{#if branch}
  <details class="tree" open={depth === 0}>
    <summary>{#if name !== null}<code class="key">{name}</code>{/if} <span class="small muted">{count}</span></summary>
    <div class="kids">
      {#each entries as [k, v] (k)}<Self value={v} name={k} depth={depth + 1} />{/each}
    </div>
  </details>
{:else}
  <div class="tree leaf">{#if name !== null}<code class="key">{name}</code>:{/if} <code class="v">{scalar(value)}</code></div>
{/if}
