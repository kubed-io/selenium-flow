<script lang="ts">
  import Modal from './Modal.svelte'
  import type { ModalSpec } from './modal'
  let { onconfirm, oncancel, onclosed, danger = false }: { onconfirm: () => Promise<void> | void; oncancel?: () => void; onclosed: (c: boolean) => void; danger?: boolean } = $props()
  const spec: ModalSpec<string[]> = $derived({ title: 'Clear <all>', body: names, data: ['a <b>', 'c'], confirm: 'Clear 2 files', danger, onconfirm, oncancel })
</script>

{#snippet names(list: string[])}<ul class="names">{#each list as n (n)}<li>{n}</li>{/each}</ul>{/snippet}
<Modal {spec} {onclosed} />
