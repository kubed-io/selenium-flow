<script lang="ts">
  import type { FlowDoc } from '../lib/types'
  import { argValue, listed, mapping } from './flow'
  import Pair from './Pair.svelte'

  let { f, i }: { f: FlowDoc; i: number } = $props()
  const steps = $derived(listed(f.steps))
  const s = $derived(mapping(steps[i]))
  const tool = $derived(String(s.tool || '?'))
  const args = $derived(Object.entries(mapping(s.args)))
</script>

<!-- No whitespace between the tags (see StepRow). The tool is omitted where
     the chip already is the tool: "navigate navigate" says nothing twice. -->
{#if i < 0 || i >= steps.length}<div class="hint">That step is gone.</div>{:else}<div
  class="dhead"><span class="n">{i + 1}</span><span class="chip">{String(s.id || tool)}</span>{#if s.id}<span
  class="ty">{tool}</span>{/if}</div>{#if s.note}<div class="hint">{String(s.note)}</div>{/if}<div
  class="dlabel">Arguments</div>{#if !args.length}<div class="hint">This step takes nothing.</div>{:else}<div
  class="pairs">{#each args as [k, v] (k)}<Pair {k} v={argValue(k, v)} />{/each}</div>{/if}<!--
  Only when set: `onError: abort` on every step would bury the one `continue`.
-->{#if s.onError || s.return}<div class="dlabel">Behaviour</div><div
  class="pairs">{#if s.onError}<Pair k="onError" v={String(s.onError)} />{/if}{#if s.return}<Pair
  k="return" v="this step’s full result" />{/if}</div>{/if}{/if}
