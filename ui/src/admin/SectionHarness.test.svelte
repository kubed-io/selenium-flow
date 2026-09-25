<script lang="ts">
  import Section from './Section.svelte'
  let { id = 'mySection' }: { id?: string } = $props()
</script>

{#snippet body()}<p>content</p>{/snippet}
<Section {id} title="Stuff" count="2" children={body} />
