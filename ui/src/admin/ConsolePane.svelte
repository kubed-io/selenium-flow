<script lang="ts">
  let { src }: { src: string } = $props()
</script>

<section id="paneConsole">
  <p class="small muted">
    The Grid's own console, same-origin in a frame. Its live view works from
    here because both halves are served from one host.
  </p>
  <iframe class="console" {src} title="Selenium Grid console"></iframe>
</section>
