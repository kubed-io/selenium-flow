<script lang="ts">
  import Section from './Section.svelte'
  let { id = 'mySection', collapsed = false }: { id?: string; collapsed?: boolean } = $props()
</script>

{#snippet body()}<p>content</p>{/snippet}
<Section {id} title="Stuff" count="2" {collapsed} children={body} />
