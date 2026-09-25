<script lang="ts">
  import Section from './Section.svelte'
</script>

{#snippet body()}<p>content</p>{/snippet}
<Section id="mySection" title="Stuff" count="2" children={body} />
