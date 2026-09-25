<script lang="ts">
  import FileGrid from '../lib/FileGrid.svelte'
  import type { FileEntry, Folder } from '../lib/types'
  import Section from './Section.svelte'
  import type { SessionModel } from './session.svelte'
  import { NO_FILES } from './session.svelte'

  let { m, root, hidden, onopen, onkeep, ondelete, onclear }: {
    m: SessionModel
    root: string
    hidden: boolean
    onopen: (folder: Folder, i: number) => void
    onkeep: (folder: Folder, f: FileEntry) => Promise<boolean>
    ondelete: (f: FileEntry) => void
    onclear: (what: 'downloads' | 'screenshots') => void
  } = $props()

  // Ending needs attached; clearing downloads needs it RUNNING — the Grid
  // deletes the download store with the browser — and this session's own files.
  const clearDownloadsOff = $derived(!m.row.live || m.files === NO_FILES || m.loadingFiles)
  // No per-screenshot delete on the Grid, so the clear is offered only when
  // there is something to take.
  const clearScreenshotsOff = $derived(!m.files.screenshots.length || m.loadingFiles)
  const count = (n: number | undefined) => (m.filesError || !m.view ? '' : String(n ?? ''))
</script>

{#snippet rows(folder: 'downloads' | 'screenshots' | 'kept')}
  {#if m.filesError}
    <div class="empty error">{m.filesError}</div>
  {:else if !m.view}
    <div class="empty">Loading…</div>
  {:else if folder === 'downloads'}
    <FileGrid files={m.view.downloads} base={root} action="keep" empty={m.view.downloadsEmpty}
              onopen={(i) => onopen('downloads', i)} onkeep={(f) => onkeep('downloads', f)} />
  {:else if folder === 'screenshots'}
    <FileGrid files={m.view.screenshots} base={root} action="keep" empty="No screenshots yet."
              onopen={(i) => onopen('screenshots', i)} onkeep={(f) => onkeep('screenshots', f)} />
  {:else}
    <FileGrid files={m.view.files} base={root} action="delete"
              empty="Nothing here yet — prints land here, and anything you keep."
              onopen={(i) => onopen('files', i)} {ondelete} />
  {/if}
{/snippet}

<div id="paneFiles" {hidden}>
  <Section id="downloadsSection" title="Downloads" count={count(m.view?.counts.downloads)}>
    {#snippet actions()}
      <button id="clearDownloads" class="danger" disabled={clearDownloadsOff}
              title="Delete everything this browser downloaded. Kept files stay."
              onclick={() => onclear('downloads')}>Clear downloads</button>
    {/snippet}
    <div id="downloads">{@render rows('downloads')}</div>
  </Section>
  <Section id="screenshotsSection" title="Screenshots" count={count(m.view?.counts.screenshots)}>
    {#snippet actions()}
      <button id="clearScreenshots" class="danger" disabled={clearScreenshotsOff}
              title="Delete every screenshot. Anything you kept is in Files and stays."
              onclick={() => onclear('screenshots')}>Clear screenshots</button>
    {/snippet}
    <div id="screenshots">{@render rows('screenshots')}</div>
  </Section>
  <!-- No clear: everything in Files was put there on purpose (§F4.1). -->
  <Section id="keptSection" title="Files" count={count(m.view?.counts.files)}>
    <div id="kept">{@render rows('kept')}</div>
  </Section>
</div>
