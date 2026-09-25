<script lang="ts">
  import { untrack } from 'svelte'
  import { sessionPath, type Api } from './api'
  import { listed, mapping } from './flow'
  import type { ModalSpec } from './modal'
  import ParamDetail from './ParamDetail.svelte'
  import ParamRow from './ParamRow.svelte'
  import type { SessionModel } from './session.svelte'
  import StepDetail from './StepDetail.svelte'
  import StepRow from './StepRow.svelte'

  /* Rendered once per SessionDetail, which is itself keyed by session, so
     every prop here belongs to one session for this component's whole life: a
     confirm that awaits and then reads `isGone`, `onclosed` or `reload` reads
     its own session's, never the one on screen since. */
  let { m, flowName, api, ask, refuseIfGone, isGone, onpick, onclosed, reload }: {
    m: SessionModel
    flowName: string | null
    api: Api
    ask: <T>(spec: ModalSpec<T>) => void
    refuseIfGone: () => void
    isGone: () => boolean
    onpick: (name: string) => void
    /* After a move or delete: the flow closes, the hash goes back to …/flows,
       and the listing reloads. Resolves once it has. */
    onclosed: () => Promise<void>
    /* After a save: the listing reloads, and with it whatever is still open. */
    reload: () => Promise<void>
  } = $props()

  // One selection across params and steps: the pane beside them shows one thing.
  let picked = $state<{ kind: 'param'; key: string } | { kind: 'step'; key: number } | null>(null)
  // A different document clears the selection; a refresh of the same one keeps
  // it (a refresh is not a click). Picking from the list clears it too, below.
  $effect(() => { void flowName; untrack(() => { picked = null }) })
  let draft = $state('')

  const data = $derived(m.flows ?? { enabled: true, flows: [] })
  const f = $derived(m.flowDoc && m.flowDoc.name === flowName ? m.flowDoc : null)
  const docError = $derived(m.flowDocError && m.flowDocError.name === flowName ? m.flowDocError.message : null)
  const declared = $derived(f ? mapping(mapping(f.parameters).properties) : {})
  const required = $derived(f ? listed(mapping(f.parameters).required) : [])
  const names = $derived(Object.keys(declared))
  const steps = $derived(f ? listed(f.steps) : [])
  // `global` is a folder like any other; a session literally named `global` IS
  // the shared library, so the move would go nowhere.
  const canMove = $derived(!m.flows || m.flows.session !== 'global')
  const moveSaid = $derived(f?.shared ? 'Move to this session' : 'Move to global')

  /* A param row and a step row are one control in two sections; the "used by"
     rows in the pane are step rows too, so a click there jumps to the step.
     Clicking the picked one unpicks it. */
  function pick(e: MouseEvent) {
    const row = (e.target as HTMLElement).closest('.rows .row') as HTMLElement | null
    if (!row) return
    const next = row.dataset.param !== undefined
      ? { kind: 'param' as const, key: row.dataset.param }
      : { kind: 'step' as const, key: Number(row.dataset.step) }
    picked = isPicked(next.kind, next.key) ? null : next
  }
  const isPicked = (kind: 'param' | 'step', key: string | number) => !!picked && picked.kind === kind && picked.key === key

  // A click, even on the flow already open: you asked for the document again.
  function choose(name: string) {
    picked = null
    onpick(name)
  }

  async function edit() {
    const doc = f!
    const path = sessionPath(m.key, '/flows/' + encodeURIComponent(doc.name))
    // Read again: the editor's Save is a blind PUT, and the copy on screen can
    // be minutes old (§F1.40 records the rest). The raw file, comments and all.
    let fresh: { yaml?: string }
    try {
      fresh = await api(path)
    } catch (err) {
      alert('Could not read that flow: ' + (err as Error).message)
      return
    }
    draft = fresh.yaml || ''
    ask({
      title: 'Edit ' + doc.name, body: editorBody, data: null, confirm: 'Save',
      onconfirm: async () => {
        refuseIfGone()
        await api(path, 'PUT', { yaml: draft })
        if (!isGone()) await reload()
      },
    })
  }

  function move() {
    const doc = f!
    const to = doc.shared ? String(m.flows!.session) : 'global'
    ask({
      title: doc.shared ? 'Move to this session' : 'Move to global', body: moveBody,
      data: { shared: !!doc.shared, to }, confirm: 'Move',
      onconfirm: async () => {
        refuseIfGone()
        await api(sessionPath(m.key, '/flows/' + encodeURIComponent(doc.name) + '/move'), 'POST', { to })
        if (!isGone()) await onclosed()
      },
    })
  }

  function drop() {
    const doc = f!
    ask({
      title: 'Delete this flow?', body: dropBody, data: (doc.shared ? 'global / ' : '') + doc.name,
      confirm: 'Delete', danger: true,
      onconfirm: async () => {
        refuseIfGone()
        await api(sessionPath(m.key, '/flows/' + encodeURIComponent(doc.name)), 'DELETE')
        if (!isGone()) await onclosed()
      },
    })
  }
</script>

{#snippet editorBody()}<textarea class="yaml" spellcheck="false" bind:value={draft}></textarea>{/snippet}
{#snippet moveBody(d: { shared: boolean; to: string })}{#if d.shared}<p>It moves out of the shared library into <strong>{d.to}</strong>. Other sessions stop seeing it.</p>{:else}<p>It moves into the shared <strong>global</strong> library, where every session can list and run it. No agent can change what is in there — only an operator, here.</p>{/if}{/snippet}
{#snippet dropBody(label: string)}<p>It is removed from the folder it lives in. A flow in the <strong>global</strong> folder goes for every session, not just this one.</p><ul class="names"><li>{label}</li></ul>{/snippet}

<!-- No whitespace between sibling tags, as today's markup: between inline
     elements it would render as a gap. A list item shows the globe only where
     it is true, at the far end so the names line up. The move is one verb,
     its icon the destination rather than the current state. Params come
     first: what the flow asks of you, then what it does with it. -->
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions (today's rows are mouse-only) -->
<div id="flows" onclick={pick}>{#if m.flowsError}<div class="empty error">{m.flowsError}</div>{:else if !m.flows}<div
  class="empty">Loading…</div>{:else if !data.enabled}<div
  class="empty">Flows are off: this server was started with no FLOW_DATA_DIR.</div>{:else if !(data.flows || []).length}<div
  class="empty">No flows yet.</div>{:else}<div class="flows"><div class="flowlist" id="flowlist">{#each data.flows || [] as item (item.name)}<!--
    svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions (today's list items are mouse-only)
    --><div class="item" aria-selected={item.name === flowName} onclick={() => choose(item.name)}><div
        class="grow"><div class="nm">{item.name}</div><div class="ct">{item.step_count} step{item.step_count === 1 ? '' : 's'}</div></div>{#if item.shared}<span
        class="globe" role="img" aria-label="global" title="In the shared global library">🌐</span>{/if}</div>{/each}</div><div
    id="flowpanel">{#if !flowName}<div class="empty">Pick a flow.</div>{:else if docError}<div
      class="empty error">{docError}</div>{:else if !f}<div class="empty">Loading…</div>{:else}<div class="panel"><div
        class="head"><span class="nm">{f.name}</span>{#if f.shared}<span class="pill">🌐 global</span>{/if}<span
          class="grow"></span><div class="acts"><button type="button" data-edit title="Edit YAML" aria-label="Edit YAML"
            onclick={edit}>✏️</button>{#if canMove}<button type="button" data-move title={moveSaid} aria-label={moveSaid}
            onclick={move}>{f.shared ? '🏠' : '🌐'}</button>{/if}<button type="button" class="danger" data-drop
            title="Delete this flow" aria-label="Delete this flow" onclick={drop}>🗑️</button></div></div>{#if f.description}<div
        class="desc">{f.description}</div>{/if}<div class="panes"><div class="outline"><div
            class="olabel">Params</div>{#if names.length}<div class="rows">{#each names as n (n)}<ParamRow
              name={n} spec={declared[n]} required={required.includes(n)} picked={isPicked('param', n)} />{/each}</div>{:else}<div
            class="none">This flow takes nothing.</div>{/if}<div class="olabel">Steps</div><div
            class="rows">{#each steps as s, i (i)}<StepRow entry={s} {i} picked={isPicked('step', i)} />{/each}</div></div><div
          class="rule"></div><div class="pane">{#if !picked}<div
            class="hint">Pick a parameter or a step to see what it holds.</div>{:else if picked.kind === 'param'}<ParamDetail
            {f} name={picked.key} />{:else}<StepDetail {f} i={picked.key} />{/if}</div></div></div>{/if}</div></div>{/if}</div>
