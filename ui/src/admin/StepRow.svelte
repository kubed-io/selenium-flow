<script lang="ts">
  import { bindsSecret, mapping, own, TOOL_ICON } from './flow'

  // `cite` is for a parameter's "used by" rows, which number themselves: there
  // the step's position is the point of showing it.
  let { entry, i, cite = false, picked }: { entry: unknown; i: number; cite?: boolean; picked: boolean } = $props()
  // `steps:` with an empty item parses to [null].
  const s = $derived(mapping(entry))
  const tool = $derived(String(s.tool || '?'))
</script>

<!-- No whitespace between the tags, as today's markup: between inline spans
     it would render as a gap. The tool's word is gone from the row, so the
     icon carries it for a screen reader and a hover. -->
<div class="row" data-step={i} aria-selected={picked}>{#if cite}<span class="n">{i + 1}</span>{/if}<span
  class="icon" role="img" aria-label={tool} title={tool}>{own(TOOL_ICON, tool) || '❓'}</span><span
  class="chip">{String(s.id || tool)}</span>{#if bindsSecret(s)}<span class="grow"></span><span
  class="lock" role="img" aria-label="types a secret" title="This step types a secret">🔒</span>{/if}</div>
