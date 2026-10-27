<script lang="ts">
  import { onMount } from 'svelte'
  import type { Api } from './api'
  import { LiveView } from './liveview.svelte'

  let { api, key, name, root, load }: {
    api: Api
    key: string
    name: string
    root: string
    load: ConstructorParameters<typeof LiveView>[0]['load']
  } = $props()

  let pane: HTMLElement
  let screen = $state.raw<HTMLElement>()
  let view = $state.raw<LiveView | null>(null)
  let full = $state(false)
  let pasting = $state(false)
  let text = $state('')
  let refused = $state('')
  let sending = $state(false)

  onMount(() => {
    const v = new LiveView({ api, key, root, target: screen!, load })
    view = v
    void v.connect()
    return () => v.dispose()
  })

  const up = $derived(view?.state === 'connected')
  const control = $derived(view?.mode === 'control')

  const closePaste = () => { pasting = false; refused = '' }
  // The popover is only ever for Control: a drop or a switch to Watch closes it.
  $effect(() => { if (!control) closePaste() })

  async function send() {
    if (!view || sending) return
    sending = true
    refused = ''
    try {
      await view.paste(text)
      closePaste()
      text = ''
    } catch (err) {
      refused = (err as Error).message
    } finally {
      sending = false
    }
  }

  const enterFullscreen = () => { pane.requestFullscreen?.()?.catch(() => {}) }
  const leaveFullscreen = () => { void document.exitFullscreen?.() }
</script>

<svelte:document
  onfullscreenchange={() => (full = document.fullscreenElement === pane)}
  onclick={(e) => { if (pasting && !(e.target as Element).closest('.pastewrap')) closePaste() }} />

{#snippet toggle()}
  <span class="switch">
    <button type="button" aria-pressed={!control} aria-label="Watch" title="Watch" onclick={() => view?.ask('watch')}>👁️</button>
    <button type="button" aria-pressed={control} aria-label="Control" title="Control" onclick={() => view?.ask('control')}>🖱️</button>
  </span>
{/snippet}

{#snippet paste()}
  <span class="pastewrap">
    <button type="button" aria-label="Paste" disabled={!control} onclick={() => (pasting ? closePaste() : (pasting = true))}
            title={control ? 'Paste' : 'Paste types into the browser in Control'}>📋</button>
    {#if pasting && control}
      <div class="popover">
        <textarea placeholder="Paste" bind:value={text}></textarea>
        <p class="small muted">Types at the cursor in the browser</p>
        {#if refused}<p class="small error">{refused}</p>{/if}
        <button type="button" class="primary" disabled={sending || !text} onclick={send}>Send</button>
      </div>
    {/if}
  </span>
{/snippet}

<div class="livepane" class:full bind:this={pane}>
  {#if view?.unavailable}
    <p class="empty">{view.unavailable}</p>
  {:else}
    <div class="livebar row toolbar">
      {#if full}<span class="name">{name}</span>{/if}
      {#if view?.state === 'connected'}
        <span class="pill live">● connected</span>
      {:else if view?.state === 'disconnected'}
        <span class="pill warn" title={view.reason}>disconnected</span>
      {:else}
        <span class="pill">connecting</span>
      {/if}
      <span class="grow"></span>
      <span class="controls row" class:dim={!up}>
        {@render toggle()}
        {@render paste()}
        {#if full}
          <button type="button" onclick={leaveFullscreen}>Close</button>
        {:else}
          <button type="button" aria-label="Fullscreen" title="Fullscreen" onclick={enterFullscreen}>⛶</button>
        {/if}
      </span>
    </div>
    <div class="screenbox">
      <div class="screen" class:connecting={view?.state === 'connecting'} class:connected={up} class:control class:down={view?.state === 'disconnected'} bind:this={screen}></div>
      {#if view?.state === 'disconnected'}
        <button type="button" class="reconnect" onclick={() => void view?.connect()}>Reconnect</button>
      {/if}
    </div>
  {/if}
</div>

<style>
  .livepane { display: flex; flex-direction: column; gap: 8px; }
  .livebar { margin-bottom: 0; }
  .controls { gap: 8px; }
  .controls.dim { opacity: 0.4; }
  .screenbox { position: relative; }
  .screen { aspect-ratio: 16 / 9; width: 100%; overflow: hidden; border: 1px solid var(--line); border-radius: var(--radius); }
  .screen.connecting { background: #F1F1F5; }
  .screen.connected { background: #000; }
  .screen.control { outline: 2px solid var(--accent); }
  .screen.down { opacity: 0.45; }
  .reconnect { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); }
  .pastewrap { position: relative; display: inline-block; }
  .popover {
    position: absolute; right: 0; top: calc(100% + 6px); z-index: 5; width: 280px;
    display: flex; flex-direction: column; gap: 8px; padding: 10px; align-items: flex-end;
    background: #fff; color: #16161d; border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 6px 20px rgba(0, 0, 0, 0.15);
  }
  .popover textarea { width: 100%; box-sizing: border-box; min-height: 80px; font: inherit; padding: 8px; border: 1px solid var(--line); border-radius: 8px; }
  .popover textarea:focus { outline: none; border-color: var(--accent); }
  .popover p { margin: 0; align-self: flex-start; }
  .full { position: fixed; inset: 0; z-index: 50; background: #2E2E2E; color: #f2f2f7; padding: 10px 16px 16px; }
  .full .name { font-size: 14px; font-weight: 600; }
  .full .screenbox { flex: 1; min-height: 0; display: grid; place-items: center; }
  .full .screen { width: min(100%, calc((100vh - 70px) * 16 / 9)); }
  .full .livebar button { background: rgba(255, 255, 255, 0.14); color: #fff; border-color: transparent; }
  .full .livebar .switch { background: none; border: 1px solid rgba(255, 255, 255, 0.3); }
  .full .livebar .switch button { background: none; color: rgba(255, 255, 255, 0.7); }
  .full .livebar .switch button[aria-pressed=true] { background: rgba(255, 255, 255, 0.22); color: #fff; }
  .full .popover button { color: var(--accent-ink); background: var(--accent); }
</style>
