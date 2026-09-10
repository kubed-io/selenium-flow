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

  /* The files one session has downloaded.
     Each entry needs {name, size, url, image}. `url` is already signed by the
     server, so this component never sees a token. */
  function fileGrid(el, data, opts = {}) {
    const files = (data && data.files) || [];
    if (!files.length) return empty(el, 'Nothing downloaded in this session yet.');
    const base = opts.base || '';
    el.innerHTML = '';
    const grid = document.createElement('div');
    grid.className = 'files';
    for (const f of files) {
      const ext = (f.name.split('.').pop() || '').toLowerCase();
      const href = base + f.url;
      const item = document.createElement('div');
      item.className = 'file';
      item.innerHTML =
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

  /* One session's headline: what it is, who holds it, where it runs. */
  function sessionSummary(el, data) {
    const s = data || {};
    const facts = [
      // Neither the name nor the key when there is a name: the heading above is
      // the name, and the key is only ever `named:<that same name>`. Shown for
      // the others, where the heading is a kind — "mcp client", "stateless" —
      // and the key is the only thing saying *which* one.
      ['session', s.name ? null : s.key],
      ['held by', s.name ? null : s.owner],
      ['browser', s.browser
        ? browserMark(s.browser) + ' ' + [s.browser, s.version].filter(Boolean).join(' ')
        : null],
      // Absent rather than "none" when detached: the session is the row, and a
      // browser is a thing it currently happens to have.
      ['browser id', s.session_id],
      ['last page', s.url],
      ['started', s.started ? new Date(s.started * 1000).toLocaleString() : null],
      ['files', s.files_count === undefined || s.files_count === null
        ? null : String(s.files_count)],
      ['node', s.node],
    ].filter(([, v]) => v);

    el.innerHTML =
      '<div class="card">' +
      '<div class="row" style="margin-bottom:10px">' +
      '<span class="bmark" title="' + esc(s.browser || 'browser') + '">' +
      browserMark(s.browser) + '</span>' +
      '<strong class="grow">' + esc(s.name || s.owner || 'Session') + '</strong>' +
      (s.live
        ? '<span class="pill live">live</span>'
        : '<span class="pill">idle</span>') +
      '</div><div class="facts">' +
      facts.map(([k, v]) =>
        '<div class="fact"><div class="k">' + esc(k) + '</div>' +
        '<div class="v' + (k === 'session' ? ' mono small' : '') + '">' +
        esc(v) + '</div></div>').join('') +
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
