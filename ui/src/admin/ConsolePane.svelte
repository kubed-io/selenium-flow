<script lang="ts">
  // `hidden`, not an `{#if}` at the call site: today's page creates this
  // iframe once at boot and only toggles its section — mounting it fresh on
  // every visit to this tab would reload the Grid's own console each time.
  let { src, hidden = false }: { src: string; hidden?: boolean } = $props()
</script>

<section id="paneConsole" {hidden}>
  <p class="small muted">
    The Grid's own console, same-origin in a frame. Its live view works from
    here because both halves are served from one host.
  </p>
  <iframe class="console" {src} title="Selenium Grid console"></iframe>
</section>
