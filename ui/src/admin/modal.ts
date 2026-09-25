import type { Snippet } from 'svelte'

/* One modal, three uses: the confirm that lists what it removes (native
   confirm() cannot show a list, and "Delete 12 files?" without saying which
   is an assertion, not a disclosure), the flow delete confirm, the YAML editor. */
export interface ModalSpec<T = unknown> {
  title: string
  body: Snippet<[T]>
  data: T
  confirm?: string
  danger?: boolean
  /* 'downloads' when it acts on the browser's downloads: a browser change
     closes it without touching session-scoped dialogs (§F4.9). */
  scope?: 'downloads' | null
  onconfirm: () => Promise<void> | void
  oncancel?: () => void
}
