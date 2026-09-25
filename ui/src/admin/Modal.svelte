<script lang="ts" generics="T">
  import type { ModalSpec } from './modal'

  // Generic over T, rather than the default `unknown`: a caller's
  // `ModalSpec<T>` for its own T (a snippet typed to the list it renders) is
  // not assignable to `ModalSpec<unknown>` — Snippet's parameter is
  // contravariant, so `unknown` is the wrong direction. This component never
  // reads `data` itself; it only hands it back to `spec.body`.
  let { spec, onclosed }: { spec: ModalSpec<T>; onclosed: (confirmed: boolean) => void } = $props()
  let busy = $state(false)

  // The first close wins. Cancel, Esc and the backdrop are never guarded by
  // `busy` — a confirm still in flight can be cancelled out from under it —
  // so without this flag a cancel that races a slow `onconfirm` let its later
  // resolution call `onclosed(true)` a second time, right after `onclosed`
  // had already been told `false`.
  let closed = false

  // Cancel, Esc, the backdrop: anything that is not a successful confirm runs
  // oncancel — without it a lightbox button disabled behind this box stayed
  // disabled forever.
  function cancel() {
    if (closed) return
    closed = true
    spec.oncancel?.()
    onclosed(false)
  }

  async function confirm() {
    busy = true
    try {
      await spec.onconfirm()
      // Cancelled while this was in flight: `onclosed(false)` already ran,
      // and calling `onclosed(true)` now would be a second, contradictory
      // close for the same modal.
      if (closed) return
      closed = true
      onclosed(true)
    } catch (err) {
      // Stay open: closing would throw away what they typed in the editor.
      // Parity with today's `modal()`: its delegated click handler has no
      // guard here either, so a cancel that races a failing confirm still
      // alerts — traced via `close()` removing the box while the original
      // click's `catch` keeps its own `e.target` and runs regardless. Kept
      // deliberately rather than silenced.
      busy = false
      alert((err as Error).message)
    }
  }
</script>

<svelte:document onkeydown={(e) => { if (e.key === 'Escape') cancel() }} />
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions (Esc is handled on the document) -->
<div class="modal" onclick={(e) => { if (e.target === e.currentTarget) cancel() }}>
  <div class="sheet">
    <div class="head">{spec.title}</div>
    <div class="body">{@render spec.body(spec.data)}</div>
    <div class="foot">
      <button type="button" onclick={cancel}>Cancel</button>
      <button type="button" class={spec.danger ? 'danger' : 'primary'} disabled={busy} onclick={confirm}>
        {spec.confirm ?? 'OK'}
      </button>
    </div>
  </div>
</div>
