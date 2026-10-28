<script lang="ts">
  import Section from './Section.svelte'
  let { id = 'mySection', collapsed = false, fold }: { id?: string; collapsed?: boolean; fold?: string } = $props()
</script>

{#snippet body()}<p>content</p>{/snippet}
<Section {id} title="Stuff" count="2" {collapsed} {fold} children={body} />
