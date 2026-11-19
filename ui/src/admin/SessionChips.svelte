<script lang="ts">
  import { tick } from 'svelte'
  import type { SessionInfo } from '../lib/types'

  /* The workspace's sessions, picked one at a time (UI-65): the default, a
     divider, then each sub-session by its label. Past MAX the rest are in a
     `+N` menu. A radio group: arrows move the pick. */
  let { sessions, picked, onpick }: { sessions: SessionInfo[]; picked: string; onpick: (name: string) => void } = $props()

  const MAX = 5
  const def = $derived(sessions.find((s) => s.default) ?? sessions[0])
  const subs = $derived(sessions.filter((s) => s !== def))
  // The picked one is never hidden in the menu: it takes the last chip's place.
  const shown = $derived.by(() => {
    const head = subs.slice(0, MAX)
    const at = subs.findIndex((s) => s.name === picked)
    if (at >= MAX) head[MAX - 1] = subs[at]
    return head
  })
  const more = $derived(subs.filter((s) => !shown.includes(s)))
  const chips = $derived(def ? [def, ...shown] : shown)

  let open = $state(false)

  function key(e: KeyboardEvent, at: number) {
    const step = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0
    if (!step) return
    e.preventDefault()
    const to = (at + step + chips.length) % chips.length
    const group = (e.currentTarget as HTMLElement).parentElement
    onpick(chips[to].name)
    // The new tab stop is drawn after the pick; focus follows it.
    void tick().then(() => group?.querySelectorAll<HTMLElement>('[role=radio]')[to]?.focus())
  }
  const stop = (s: SessionInfo) => (s.name === picked ? 0 : -1)
</script>

{#snippet chip(s: SessionInfo, at: number)}
  <button type="button" class="chip" role="radio" aria-checked={s.name === picked} tabindex={stop(s)}
          onclick={() => onpick(s.name)} onkeydown={(e) => key(e, at)}>
    <span class={['dot', s.live && 'live']} role="img" aria-label={s.live ? 'live' : 'not live'}></span>
    <span class="name">{s.label}</span>
    {#if s.browser}<span class="meta">{s.browser}</span>{/if}
  </button>
{/snippet}

<div class="chips" role="radiogroup" aria-label="Session">
  {#if def}{@render chip(def, 0)}{/if}
  <span class="divider" aria-hidden="true"></span>
  <span class="label small muted">Sub-sessions</span>
  {#each shown as s, i (s.name)}{@render chip(s, i + 1)}{/each}
  {#if more.length}
    <span class="more">
      <button type="button" class="chip" aria-haspopup="menu" aria-expanded={open} onclick={() => (open = !open)}>+{more.length}</button>
      {#if open}
        <div class="menu" role="menu">
          {#each more as s (s.name)}
            <button type="button" role="menuitem" onclick={() => { open = false; onpick(s.name) }}>
              <span class={['dot', s.live && 'live']} role="img" aria-label={s.live ? 'live' : 'not live'}></span>{s.label}
            </button>
          {/each}
        </div>
      {/if}
    </span>
  {/if}
</div>

<style>
  .chips { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
  .chip { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; min-height: 28px; border-radius: 999px; font-size: 13px; }
  .chip[aria-checked=true] { border-color: var(--accent); color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, transparent); }
  /* app.css hovers a chip in the accent, which is what selected looks like here:
     with the pointer left on another chip, two read as selected. */
  .chip:not([aria-checked=true]):hover { border-color: var(--muted); }
  .meta { font-size: 12px; color: var(--muted); }
  .dot { width: 8px; height: 8px; border-radius: 50%; border: 1.5px solid var(--muted); box-sizing: border-box; }
  .dot.live { background: var(--ok); border-color: var(--ok); }
  .divider { align-self: stretch; width: 1px; margin: 4px 2px; background: var(--line); }
  .more { position: relative; }
  .menu { position: absolute; z-index: 5; top: 100%; left: 0; margin-top: 4px; display: grid; min-width: 140px; padding: 4px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }
  .menu button { display: flex; align-items: center; gap: 6px; border: none; background: none; text-align: left; padding: 6px 8px; }
</style>
