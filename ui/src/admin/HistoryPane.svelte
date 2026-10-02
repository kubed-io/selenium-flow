<script lang="ts">
  import { untrack } from 'svelte'
  import { ago } from '../lib/format'
  import type { HistoryRow, SavedCounts } from '../lib/types'
  import { sessionPath, type Api } from './api'
  import { folds } from './folds.svelte'
  import type { ModalSpec } from './modal'
  import { go, hashes } from './router.svelte'
  import Section from './Section.svelte'
  import type { SessionModel } from './session.svelte'

  let { m, api, ask, refuseIfGone, isGone, hidden }: {
    m: SessionModel
    api: Api
    ask: <T>(spec: ModalSpec<T>) => void
    refuseIfGone: () => void
    isGone: () => boolean
    hidden: boolean
  } = $props()

  const data = $derived(m.history)
  // Ends in `Section`: Section derives its body's id by replacing that.
  const idOf = (r: HistoryRow) => 'visit-' + m.key + ':' + r.site + 'Section'

  // The top row opens, the rest stay shut — once; after that the fold is the
  // reader's, as on Site data.
  $effect.pre(() => {
    const sites = data?.sites ?? []
    untrack(() => sites.forEach((r, i) => { folds[idOf(r)] ??= i === 0 }))
  })

  const plural = (n: number, one: string) => n + ' ' + one + (n === 1 ? '' : 's')
  // Zero counts are left out: "2 cookies · 2 local".
  const savedText = (s: SavedCounts) => [
    s.cookies ? plural(s.cookies, 'cookie') : '',
    s.local ? s.local + ' local' : '',
    s.session ? s.session + ' session' : '',
  ].filter(Boolean).join(' · ')
  const when = (r: HistoryRow) =>
    [ago(r.at * 1000), r.secrets.length ? plural(r.secrets.length, 'secret') : ''].filter(Boolean).join(' · ')

  interface Clear { goes: string[]; stays: string }

  function clear() {
    const sites = data?.sites ?? []
    ask<Clear>({
      title: 'Clear history',
      body: clearBody,
      data: { goes: data?.clears ?? [], stays: sites[0]?.site ?? '' },
      confirm: 'Clear', danger: true,
      onconfirm: async () => {
        refuseIfGone()
        await api(sessionPath(m.key, '/history'), 'DELETE')
        // Site data is ordered by the history, so it moves too.
        if (!isGone()) { void m.loadHistory(); void m.loadSiteData() }
      },
    })
  }
</script>

{#snippet clearBody(c: Clear)}
  <p>History only: site data and the browser are untouched.</p>
  <div class="pairs fate-list">
    <div class="pair"><span class="pk">goes</span><span class="pv">{c.goes.join(' · ')}</span></div>
    <div class="pair"><span class="pk">stays</span><span class="pv">{c.stays}, the current site</span></div>
  </div>
{/snippet}

{#snippet about(r: HistoryRow)}
  {@const saved = r.saved ? savedText(r.saved) : ''}
  <span class="small muted">{when(r)}</span>
  {#if saved}
    <!-- The counts alone read the same on every row to a screen reader. -->
    <button class="pill link" aria-label="{r.site} in Site data: {saved}" onclick={() => go(hashes.site(m.key, r.site))}>{saved}</button>
  {/if}
{/snippet}

<div id="paneHistory" {hidden}>
  {#if m.historyError}
    <div class="empty error">{m.historyError}</div>
  {:else if !data}
    <div class="empty">Loading…</div>
  {:else if !data.sites.length}
    <div class="empty">Nowhere yet.</div>
  {:else}
    {#if data.clears?.length}
      <div class="row bar">
        <span class="grow"></span>
        <button id="clearHistory" class="danger" onclick={clear}>Clear</button>
      </div>
    {/if}
    {#each data.sites as r (r.site)}
      {#if r.secrets.length}
        <Section id={idOf(r)} title={r.url}>
          {#snippet summary()}{@render about(r)}{/snippet}
          {#each r.secrets as s (s.name)}
            <div class="line secret">
              <span class="key"><span>🔑</span> <code>{s.name}</code></span>
              <span class="value">{s.description ?? ''}</span>
              <span class="flags">{#each s.keys as k (k)}<span class="pill">{k}</span>{/each}</span>
            </div>
          {/each}
        </Section>
      {:else}
        <!-- Nothing to open: the head alone, the caret's place kept so the
             URLs line up. -->
        <section class="section" id={idOf(r)}>
          <div class="head">
            <span class="title"><span class="caret gap" aria-hidden="true">▾</span>{r.url}</span>
            {@render about(r)}
          </div>
        </section>
      {/if}
    {/each}
  {/if}
</div>

<style>
  .bar { margin-bottom: 12px; }
  .caret.gap { visibility: hidden; }
  .pill.link { font: inherit; font-size: 12px; background: none; cursor: pointer; }
  .pill.link:hover { color: var(--accent); border-color: currentColor; }
  .line { display: grid; grid-template-columns: 240px 1fr auto; align-items: center; gap: 8px; padding: 6px 0; border-top: 1px solid var(--line); }
  .key { font-size: 12px; overflow-wrap: anywhere; }
  .value { overflow-wrap: anywhere; min-width: 0; }
  .flags { display: inline-flex; gap: 4px; justify-content: flex-end; }
  .fate-list { padding: 8px 10px; border-radius: 8px; background: color-mix(in srgb, var(--ink) 5%, transparent); }
  .fate-list .pk { width: 48px; }
</style>
