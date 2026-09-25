<script lang="ts">
  import type { FlowDoc } from '../lib/types'
  import { listed, mapping, own } from './flow'
  import Pair from './Pair.svelte'
  import StepRow from './StepRow.svelte'

  let { f, name }: { f: FlowDoc; name: string } = $props()
  const declared = $derived(mapping(mapping(f.parameters).properties))
  // `term:` with nothing after it is declared, and says nothing about itself.
  const spec = $derived(mapping(own(declared, name)))
  const required = $derived(listed(mapping(f.parameters).required).includes(name))
  const steps = $derived(listed(f.steps))
  const used = $derived(listed(own(mapping(f.uses), name)) as number[])
  const fallback = $derived(typeof spec.default === 'object' ? JSON.stringify(spec.default) : String(spec.default))
</script>

<!-- No whitespace between the tags (see StepRow). "Used by" is rows, not
     prose: clicking one jumps to the step that reads this. They are keyed by
     position: `uses` comes over the wire, and a repeated index renders twice,
     as today, rather than throwing. -->
{#if !Object.hasOwn(declared, name)}<div class="hint">That parameter is gone.</div>{:else}<div
  class="dhead"><span class="chip">{name}</span>{#if spec.type}<span class="ty">{String(spec.type)}</span>{/if}<span
  class="grow"></span><span class="pill">{required ? 'required' : 'optional'}</span></div>{#if spec.description}<div
  class="hint">{String(spec.description)}</div>{/if}{#if spec.default !== undefined}<div
  class="dlabel">Default</div><div class="pairs"><Pair k="default" v={fallback} /></div>{/if}<div
  class="dlabel">Used by</div>{#if used.length}<div class="rows">{#each used as i, at (at)}<StepRow
  entry={steps[i] || {}} {i} cite picked={false} />{/each}</div>{:else}<div class="hint">No step reads <code>{'${' + name + '}'}</code>. Supplying it would change nothing.</div>{/if}{/if}
