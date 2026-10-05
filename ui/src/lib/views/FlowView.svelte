<script lang="ts">
  import { flowParams, isRecord, stepSummary } from '../format'
  import type { FlowData, FlowParam, FlowStep } from '../types'

  let { data, expanded = false }: { data: FlowData; onshow?: (uri: string) => void; expanded?: boolean } = $props()
  const LIMIT = 6
  // The file as written: a hand-edited flow can hold anything (see `flowParams`).
  const declared = $derived(flowParams(data.parameters))
  const params = $derived(Object.entries(declared.properties).map(([n, p]) => [n, (isRecord(p) ? p : {}) as FlowParam] as const))
  const steps = $derived<unknown[]>(Array.isArray(data.steps) ? data.steps : [])
  const shown = $derived(expanded ? steps : steps.slice(0, LIMIT))
  const more = $derived(steps.length - shown.length)
  const step = (s: unknown) => (isRecord(s) ? (s as FlowStep) : null)
  const text = (v: unknown) => (typeof v === 'string' ? v : '')
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
          {#if text(p.type)}<span class="pill">{text(p.type)}</span>{/if}
          {#if p.default !== undefined}<span class="small muted">= {dflt(p.default)}</span>{/if}
          {#if declared.required.includes(name)}<span class="pill warn">required</span>{/if}
        </li>
      {/each}
    </ul>
  {/if}

  {#if steps.length}
    <div class="label small muted" style="margin-top:12px">steps</div>
    <ol class="list steps">
      {#each shown as raw, i (i)}
        {@const s = step(raw)}
        <li class="step">
          {#if s}
            <span>{i + 1}. <code>{text(s.tool)}</code>{#if stepSummary(s.args)}{' — ' + stepSummary(s.args)}{/if}</span>
            {#if text(s.id)}<span class="pill">{text(s.id)}</span>{/if}
            {#if text(s.note)}<span class="small muted">{text(s.note)}</span>{/if}
            {#if s.onError === 'continue'}<span class="pill warn">onError: continue</span>{/if}
          {:else}
            <span class="muted">{i + 1}. (not a step)</span>
          {/if}
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
