<script lang="ts">
  import { mapping, own, TYPE_ICON } from './flow'

  let { name, spec, required, picked }: { name: string; spec: unknown; required: boolean; picked: boolean } = $props()
  const ty = $derived(String(mapping(spec).type || ''))
  const said = $derived(ty || 'any type')
</script>

<!-- Required is a star against the name: it qualifies the name, and costs no
     width. No whitespace between the tags (see StepRow). -->
<div class="row" data-param={name} aria-selected={picked}><span
  class="icon" role="img" aria-label={said} title={said}>{own(TYPE_ICON, ty) || '·'}</span><span
  class="chip">{name}{#if required}<span class="req" role="img" aria-label="required" title="required">*</span>{/if}</span></div>
