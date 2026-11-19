<script lang="ts">
  import FileGrid from '../lib/FileGrid.svelte'
  import type { FileEntry, Folder } from '../lib/types'
  import Section from './Section.svelte'
  import type { WorkspaceModel } from './workspace.svelte'
  import { NO_FILES } from './workspace.svelte'

  let { m, root, hidden, onopen, onkeep, ondelete, onclear }: {
    m: WorkspaceModel
    root: string
    hidden: boolean
    onopen: (folder: Folder, i: number, session?: string) => void
    onkeep: (folder: Folder, f: FileEntry, session?: string) => Promise<boolean>
    ondelete: (f: FileEntry) => void
    onclear: (what: 'downloads' | 'screenshots' | 'recordings', session?: string) => void
  } = $props()

  // Ending needs attached; clearing downloads needs it RUNNING — the Grid
  // deletes the download store with the browser — and this workspace's own files.
  const sessions = $derived(m.row.sessions ?? [])
  const clearDownloadsOff = $derived(!m.row.live || m.files === NO_FILES || m.loadingFiles)
  // A download is its browser's (WS-52): one group per session, labelled, when there is more than one.
  const groups = $derived(sessions.length > 1
    ? sessions.map((x) => ({ name: x.name, label: x.label, files: x.default ? (m.view?.downloads ?? []) : (m.subDownloads[x.name] ?? []), isDefault: x.default, live: x.live }))
    : [])
  const downloadsCount = $derived(groups.length ? groups.reduce((n, g) => n + g.files.length, 0) : m.view?.counts.downloads)
  // No per-screenshot delete on the Grid, so the clear is offered only when
  // there is something to take.
  const clearRecordingsOff = $derived(!m.files.recordings.length || m.loadingFiles)
  const clearScreenshotsOff = $derived(!m.files.screenshots.length || m.loadingFiles)
  const count = (n: number | undefined) => (m.filesError || !m.view ? '' : String(n ?? ''))
</script>

{#snippet rows(folder: 'downloads' | 'screenshots' | 'recordings' | 'kept')}
  {#if m.filesError}
    <div class="empty error">{m.filesError}</div>
  {:else if !m.view}
    <div class="empty">Loading…</div>
  {:else if folder === 'downloads' && groups.length}
    {#each groups as g (g.name)}
      <div class="group" data-session={g.name}>
        <div class="row ghead">
          <div class="label grow">{g.label}</div>
          <button id={'clearDownloads-' + g.name} class="danger" disabled={!g.live || m.files === NO_FILES || m.loadingFiles}
                  title={`Delete everything the ${g.label} browser downloaded. Kept files stay.`}
                  onclick={() => onclear('downloads', g.name)}>Clear</button>
        </div>
        <FileGrid files={g.files} base={root} action="keep" empty={g.isDefault ? m.view.downloadsEmpty : 'No downloads.'}
                  onopen={(i) => onopen('downloads', i, g.isDefault ? undefined : g.name)}
                  onkeep={(f) => onkeep('downloads', f, g.isDefault ? undefined : g.name)} />
      </div>
    {/each}
  {:else if folder === 'downloads'}
    <FileGrid files={m.view.downloads} base={root} action="keep" empty={m.view.downloadsEmpty}
              onopen={(i) => onopen('downloads', i)} onkeep={(f) => onkeep('downloads', f)} />
  {:else if folder === 'screenshots'}
    <FileGrid files={m.view.screenshots} base={root} action="keep" empty="No screenshots yet."
              onopen={(i) => onopen('screenshots', i)} onkeep={(f) => onkeep('screenshots', f)} />
  {:else if folder === 'recordings'}
    <FileGrid files={m.view.recordings} base={root} action="keep" empty="No recordings yet."
              onopen={(i) => onopen('recordings', i)} onkeep={(f) => onkeep('recordings', f)} />
  {:else}
    <FileGrid files={m.view.files} base={root} action="delete"
              empty="Nothing here yet — prints land here, and anything you keep."
              onopen={(i) => onopen('files', i)} {ondelete} />
  {/if}
{/snippet}

<div id="paneFiles" {hidden}>
  <Section id="downloadsSection" title="Downloads" count={count(downloadsCount)}>
    {#snippet actions()}
      {#if !groups.length}
        <button id="clearDownloads" class="danger" disabled={clearDownloadsOff}
                title="Delete everything this browser downloaded. Kept files stay."
                onclick={() => onclear('downloads')}>Clear downloads</button>
      {/if}
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

  <Section id="recordingsSection" title="Recordings" count={count(m.view?.counts.recordings)}>
    {#snippet actions()}
      <button id="clearRecordings" class="danger" disabled={clearRecordingsOff}
              title="Delete every recording. Anything you kept is in Files and stays."
              onclick={() => onclear('recordings')}>Clear recordings</button>
    {/snippet}
    <div id="recordings">{@render rows('recordings')}</div>
  </Section>
  <!-- No clear: everything in Files was put there on purpose (§F4.1). -->
  <Section id="keptSection" title="Files" count={count(m.view?.counts.files)}>
    <div id="kept">{@render rows('kept')}</div>
  </Section>
</div>

<style>
  .group + .group { margin-top: var(--gap); }
</style>
