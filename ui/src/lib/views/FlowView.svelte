<script lang="ts">
  import { stepSummary } from '../format'
  import type { FlowData } from '../types'

  let { data, expanded = false }: { data: FlowData; onshow?: (uri: string) => void; expanded?: boolean } = $props()
  const LIMIT = 6
  const params = $derived(Object.entries(data.parameters?.properties ?? {}))
  const steps = $derived(data.steps ?? [])
  const shown = $derived(expanded ? steps : steps.slice(0, LIMIT))
  const more = $derived(steps.length - shown.length)
  const required = (n: string) => data.parameters?.required?.includes(n) ?? false
  const dflt = (v: unknown) => (typeof v === 'string' ? v : JSON.stringify(v))
</script>

<div class="card">
  <div class="row">
    <strong class="grow">{data.name}</strong>
    {#if data.shared}<span class="pill" title="shared">🌐</span>{/if}
  </div>
  {#if data.description}<div class="small muted" style="margin-top:6px">{data.description}</div>{/if}

  {#if params.length}
    <div class="label small muted" style="margin-top:12px">parameters</div>
    <ul class="list">
      {#each params as [name, p] (name)}
        <li class="param">
          <code>{name}</code>
          {#if p.type}<span class="pill">{p.type}</span>{/if}
          {#if p.default !== undefined}<span class="small muted">= {dflt(p.default)}</span>{/if}
          {#if required(name)}<span class="pill warn">required</span>{/if}
        </li>
      {/each}
    </ul>
  {/if}

  {#if steps.length}
    <div class="label small muted" style="margin-top:12px">steps</div>
    <ol class="list steps">
      {#each shown as s, i (i)}
        <li class="step">
          <span>{i + 1}. <code>{s.tool}</code>{#if stepSummary(s.args)}{' — ' + stepSummary(s.args)}{/if}</span>
          {#if s.id}<span class="pill">{s.id}</span>{/if}
          {#if s.note}<span class="small muted">{s.note}</span>{/if}
          {#if s.onError === 'continue'}<span class="pill warn">onError: continue</span>{/if}
        </li>
      {/each}
    </ol>
    {#if more > 0}<div class="small muted">+{more} more</div>{/if}
  {/if}
</div>

<style>
  .list { list-style: none; margin: 4px 0 0; padding: 0; }
  .param, .step { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; padding: 3px 0; }
  .step { word-break: break-word; }
</style>
