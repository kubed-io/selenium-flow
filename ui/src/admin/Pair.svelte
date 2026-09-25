<script lang="ts">
  let { k, v }: { k: string; v: string } = $props()
</script>

<!-- A value that spans lines is code (`script` is the one that does it): a
     table cell collapses its newlines, so it gets the full width and keeps its
     own whitespace. The test is the value, not the key. -->
{#if !v.includes('\n')}
  <div class="pair"><span class="pk">{k}</span><span class="pv">{v}</span></div>
{:else}
  <div class="pair block"><span class="pk">{k}</span><pre class="pv code">{v}</pre></div>
{/if}
