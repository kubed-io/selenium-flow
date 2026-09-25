<script lang="ts" generics="T">
  import type { ModalSpec } from './modal'

  // Generic over T, rather than the default `unknown`: a caller's
  // `ModalSpec<T>` for its own T (a snippet typed to the list it renders) is
  // not assignable to `ModalSpec<unknown>` — Snippet's parameter is
  // contravariant, so `unknown` is the wrong direction. This component never
  // reads `data` itself; it only hands it back to `spec.body`.
  let { spec, onclosed }: { spec: ModalSpec<T>; onclosed: (confirmed: boolean) => void } = $props()
  let busy = $state(false)

  // Cancel, Esc, the backdrop: anything that is not a successful confirm runs
  // oncancel — without it a lightbox button disabled behind this box stayed
  // disabled forever.
  function cancel() {
    spec.oncancel?.()
    onclosed(false)
  }

  async function confirm() {
    busy = true
    try {
      await spec.onconfirm()
      onclosed(true)
    } catch (err) {
      // Stay open: closing would throw away what they typed in the editor.
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
