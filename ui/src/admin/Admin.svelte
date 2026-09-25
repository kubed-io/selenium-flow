<script lang="ts">
  let { mount, console: consoleUrl }: { mount: string; console: string } = $props()
</script>

<p data-mount={mount} data-console={consoleUrl}>selenium-flow</p>
