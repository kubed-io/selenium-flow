/* The component library.

   These are the shared unit between the two surfaces. Each function renders one
   piece of UI from plain data into an element, and knows nothing about where
   the data came from — no fetching, no auth, no transport.

   That is what lets the same code serve both callers. An MCP app mounts exactly
   one of these and is handed its data by the host; the admin dashboard mounts
   several and fetches its own. Neither owns the markup, so the two cannot
   drift into different-looking versions of the same table. */

const SF = (() => {
  const GLYPH = {
    pdf: '📄', png: '🖼️', jpg: '🖼️', jpeg: '🖼️', gif: '🖼️', webp: '🖼️', svg: '🖼️',
    html: '🌐', htm: '🌐', csv: '📊', json: '📊', xml: '📊',
    zip: '🗜️', gz: '🗜️', mp4: '🎬', webm: '🎬', mov: '🎬', txt: '📝', md: '📝',
  };

  /* One mark per browser, so a session says which it is at a glance rather
     than only in the text beside it. Keyed on the capability the Grid reports,
     which is the browser actually running — not what was asked for. */
  const BROWSER = {chrome: '🟢', firefox: '🦊', msedge: '🌊', edge: '🌊', safari: '🧭'};

  const browserMark = (name) => BROWSER[String(name ?? '').toLowerCase()] || '🌐';

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
    (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

  /* A URL is only ever put in an href when it is one we would follow. The page
     a session last visited is whatever it navigated to, and `javascript:` is a
     URL too — escaping makes it safe to *display*, not safe to click. */
  const safeHref = (u) => (/^https?:\/\//i.test(String(u ?? '')) ? String(u) : '');

  function bytes(n) {
    if (!Number.isFinite(n)) return '';
    if (n < 1024) return n + ' B';
    if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1048576).toFixed(1) + ' MB';
  }

  function ago(ms) {
    if (!ms) return '';
    const s = Math.max(0, (Date.now() - ms) / 1000);
    if (s < 60) return Math.round(s) + 's ago';
    if (s < 3600) return Math.round(s / 60) + 'm ago';
    return Math.round(s / 3600) + 'h ago';
  }

  const empty = (el, text, cls = '') =>
    (el.innerHTML = '<div class="empty ' + cls + '">' + esc(text) + '</div>');

  /* A list of live browser sessions.
     opts.onpick — called with a session key when a row is chosen. Omit it and
     the rows render as plain, non-interactive summaries, which is what an app
     embedded in a transcript wants.

     Nothing here renders an action button. Acting on a session happens in the
     detail view, where the page already has a toolbar for it — a button inside
     a card sits next to the status pill and makes that pill look clickable
     too. These components also render inside an MCP app that holds no
     credential, where any action button would be dead. */
  function sessionList(el, data, opts = {}) {
    const sessions = (data && data.sessions) || [];
    if (!sessions.length) return empty(el, 'No sessions yet.');
    el.innerHTML = '';
    for (const s of sessions) {
      const card = document.createElement('div');
      card.className = 'card' + (opts.onpick ? ' click' : '');
      const count = s.files_count;
      // The headline is the session, not the browser: a session outlives the
      // browsers it holds, so a name or its kind identifies it and the browser
      // id is detail. A detached one is idle, not broken — it kept its context
      // and its next open picks that up.
      const label = s.name || (s.owner ? s.owner : 'session');
      card.innerHTML =
        '<div class="row">' +
        '<span class="bmark" title="' + esc(s.browser || 'browser') + '">' +
        browserMark(s.browser) + '</span>' +
        '<span class="pill name">' + esc(label) + '</span>' +
        '<span class="mono grow small muted">' +
        esc(s.session_id || 'no browser') + '</span>' +
        (s.live
          ? '<span class="pill live">live</span>'
          : '<span class="pill">idle</span>') +
        '</div>' +
        '<div class="small muted" style="margin-top:6px">' +
        esc([s.browser, s.version].filter(Boolean).join(' ')) +
        (count === undefined || count === null
          ? '' : ' · ' + count + ' file' + (count === 1 ? '' : 's')) +
        (s.started ? ' · ' + ago(s.started * 1000) : '') +
        (s.node ? ' · ' + esc(s.node) : '') + '</div>' +
        (s.url ? '<div class="small muted url">' + esc(s.url) + '</div>' : '');
      if (opts.onpick) card.onclick = () => opts.onpick(s.key);
      el.appendChild(card);
    }
  }

  /* Every file one session has — the browser's downloads and the files kept
     beyond it — as one list.

     Each entry needs {name, size, url, image, kept}. `url` is already signed by
     the server, so this component never sees a token.

     The corner mark says which kind it is, and doubles as the control for
     changing that: a bubble is a download (click to keep), a pin is a kept file
     (hover for the trash). It is rendered as data attributes rather than
     buttons, and is inert unless `opts.actions` is set — these same tiles are
     drawn inside an MCP app that holds no credential, where a live control
     would be a button that cannot work. The page that can act wires the clicks;
     this library stays rendering-only. */
  function fileGrid(el, data, opts = {}) {
    const files = (data && data.files) || [];
    if (!files.length) return empty(el, 'No files in this session yet.');
    const base = opts.base || '';
    el.innerHTML = '';
    const grid = document.createElement('div');
    grid.className = 'files' + (opts.actions ? ' can-act' : '');
    /* On a surface that can act, a mark is the only control for keeping or
       deleting — so it has to be operable without a mouse. It is still a span:
       this library renders and never wires, and the host that turned actions on
       owns the handler. Off, the mark is decoration and must NOT be focusable:
       a tab stop that does nothing is worse than no tab stop. */
    const act = (label) => (opts.actions
      ? ' role="button" tabindex="0" aria-label="' + esc(label) + '"'
      : '');
    for (const f of files) {
      const ext = (f.name.split('.').pop() || '').toLowerCase();
      const href = base + f.url;
      const item = document.createElement('div');
      item.className = 'file';
      item.innerHTML =
        (f.kept
          ? '<span class="mark pin" data-delete="' + esc(f.name) + '"' +
            act('Delete kept file ' + f.name) + ' title="' +
            'Kept: it outlives this browser. Hover to delete it.">' +
            '<span class="icon">📌</span><span class="trash">🗑</span></span>'
          : '<span class="mark bubble" data-keep="' + esc(f.name) + '"' +
            act('Keep ' + f.name + ' beyond this browser') + ' title="' +
            'A download: it goes when this browser does. Click to keep it."></span>') +
        '<a class="thumb" href="' + esc(href) + '" target="_blank" rel="noopener">' +
        (f.image
          ? '<img loading="lazy" alt="' + esc(f.name) + '" src="' + esc(href) + '">'
          : '<span class="glyph">' + (GLYPH[ext] || '📁') + '</span>') +
        '</a>' +
        '<div class="meta"><div class="name">' + esc(f.name) + '</div>' +
        '<div class="small muted">' + bytes(f.size) +
        (f.created ? ' · ' + ago(f.created) : '') + '</div></div>';
      // The anchor keeps its href so middle-click and "open in new tab" still
      // work; a plain click is intercepted for the viewer.
      item.querySelector('.thumb').addEventListener('click', (e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        lightbox(f, href);
      });
      grid.appendChild(item);
    }
    el.appendChild(grid);
  }

  /* One session's headline, grouped by how long each fact lives.

     The previous version put seven facts in one grid and buried the two that
     matter. Now: identity, then the page it is on — a full row of its own,
     because a URL is twenty characters or two hundred and it is the thing you
     actually came to read — then two blocks with *different lifetimes*. What
     the SESSION keeps survives its browser; what the BROWSER has goes with it.

     The file count is gone from here on purpose: the Files section below
     carries it, and saying it twice invites the two to disagree. */
  function sessionSummary(el, data) {
    const s = data || {};
    const group = (label, facts) => {
      const rows = facts.filter(([, v]) => v);
      if (!rows.length) return '';
      return '<div class="group"><div class="label">' + esc(label) + '</div>' +
        rows.map(([k, v]) =>
          '<div class="fact"><div class="k">' + esc(k) + '</div>' +
          '<div class="v">' + esc(v) + '</div></div>').join('') + '</div>';
    };
    const href = safeHref(s.url);

    el.innerHTML =
      '<div class="card">' +
      '<div class="row" style="margin-bottom:10px">' +
      '<span class="bmark" title="' + esc(s.browser || 'browser') + '">' +
      browserMark(s.browser) + '</span>' +
      '<strong class="grow">' + esc(s.name || s.owner || 'Session') + '</strong>' +
      (s.live
        ? '<span class="pill live">live</span>'
        : '<span class="pill">idle</span>') +
      '</div>' +
      '<div class="lastpage"><div class="k small muted">last page</div>' +
      (href
        ? '<a href="' + esc(href) + '" target="_blank" rel="noopener noreferrer">' +
          esc(s.url) + '</a>'
        : '<span class="small muted">' +
          esc(s.url || 'nowhere yet') + '</span>') +
      '</div><div class="groups">' +
      // Kept by the session, and inherited by whatever browser it opens next.
      group('session', [
        // Neither the name nor the key when there is a name: the heading above
        // is the name, and the key is only ever `named:<that same name>`. Shown
        // for the others, where the heading is a kind — "mcp client",
        // "stateless" — and the key is the only thing saying *which* one.
        ['key', s.name ? null : s.key],
        ['held by', s.name ? null : s.owner],
        ['browser', s.browser],
        // Absent when the session never named a size: the window is then
        // whatever the Grid node's default is, and printing a number would
        // claim we knew which.
        ['window', s.window],
        ['started', s.started ? new Date(s.started * 1000).toLocaleString() : null],
      ]) +
      // Gone the moment this browser ends, which is why it is a block of its
      // own rather than three more cells in one undifferentiated grid.
      group('browser', [
        ['version', s.version],
        ['id', s.session_id],
        ['node', s.node],
      ]) +
      '</div></div>';
  }

  /* Look at a file without leaving the page.

     A stored file is opened to be looked at far more often than to be kept, and
     a new tab loses the list you were reading. Images and PDFs render here;
     anything else has nothing to show, so it downloads as before. */
  function lightbox(file, href) {
    const kind = file.content_type || '';
    const viewable = file.image || kind === 'application/pdf';
    if (!viewable) { window.open(href, '_blank', 'noopener'); return; }

    const box = document.createElement('div');
    box.className = 'lightbox';
    box.innerHTML =
      '<div class="head"><span class="grow">' + esc(file.name) + '</span>' +
      '<a href="' + esc(href) + '" download="' + esc(file.name) + '">Download</a>' +
      '<button type="button" data-close>Close</button></div>' +
      '<div class="body">' +
      (file.image
        ? '<img alt="' + esc(file.name) + '" src="' + esc(href) + '">'
        : '<iframe title="' + esc(file.name) + '" src="' + esc(href) +
          '" style="width:100%;height:100%;border:0;background:#fff;border-radius:6px"></iframe>') +
      '</div>';

    function close() {
      box.remove();
      document.removeEventListener('keydown', onKey);
    }
    function onKey(e) { if (e.key === 'Escape') close(); }

    // Click the backdrop or the button, or press Escape — three ways out,
    // because a viewer you cannot dismiss is worse than a new tab.
    box.addEventListener('click', (e) => {
      if (e.target === box || e.target.hasAttribute('data-close')) close();
    });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(box);
  }

  return {sessionList, fileGrid, sessionSummary, lightbox, bytes, ago, esc};
})();
