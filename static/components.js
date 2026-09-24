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
      // The session's own tally when it has one — downloads, screenshots and
      // kept files can each be zero without the others being, so each gets
      // its own word rather than one folded "files" count. Older rows carry
      // only files_count, from before the three folders existed.
      const counts = s.counts;
      const meta = counts
        ? [[counts.downloads, ' download'], [counts.screenshots, ' screenshot'], [counts.files, ' file']]
            .filter(([n]) => n)
            .map(([n, word]) => n + word + (n === 1 ? '' : 's'))
            .join(' · ')
        : (s.files_count === undefined || s.files_count === null
            ? '' : s.files_count + ' file' + (s.files_count === 1 ? '' : 's'));
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
        (meta ? ' · ' + meta : '') +
        (s.started ? ' · ' + ago(s.started * 1000) : '') +
        (s.node ? ' · ' + esc(s.node) : '') + '</div>' +
        (s.url ? '<div class="small muted url">' + esc(s.url) + '</div>' : '');
      if (opts.onpick) card.onclick = () => opts.onpick(s.key);
      el.appendChild(card);
    }
  }

  /* One folder's files as a grid of tiles.

     `files` is a plain array — a download's, a screenshot's, or Files' own —
     each entry needing {name, size, url, image}. `url` is already signed by
     the server, so this component never sees a token.

     A tile carries no status mark: what a file *is* used to sit in a corner
     of its own, and it named a state nobody could act on, which is not worth
     a permanent mark. The row already says which folder it came from.

     The one thing a tile can offer is `opts.action` — 'keep' or 'delete',
     never both — rendered top-left as a real `<button>`, visible on hover and
     always on touch. It is omitted entirely without one: these same tiles are
     drawn inside an MCP app that holds no credential, where a control would
     be a button that cannot work. This library only ever renders that button;
     the page that can act wires the click. */
  function fileGrid(el, files, opts = {}) {
    files = files || [];
    if (!files.length) return empty(el, opts.empty || 'No files in this session yet.');
    const base = opts.base || '';
    el.innerHTML = '';
    const grid = document.createElement('div');
    grid.className = 'files';
    files.forEach((f, i) => {
      const ext = (f.name.split('.').pop() || '').toLowerCase();
      const href = base + f.url;
      const item = document.createElement('div');
      item.className = 'file';
      item.innerHTML =
        (opts.action === 'keep'
          ? '<button type="button" class="act keep" data-keep="' + esc(f.name) +
            '" aria-label="Keep ' + esc(f.name) + ' beyond this browser" title="' +
            'Keep it beyond this browser">&#128204;</button>'
          : opts.action === 'delete'
            ? '<button type="button" class="act drop" data-delete="' + esc(f.name) +
              '" aria-label="Delete ' + esc(f.name) + '" title="Delete this file">' +
              '&#128465;</button>'
            : '') +
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
        if (opts.onopen) opts.onopen(i);
        else lightbox(files, i, {base});
      });
      grid.appendChild(item);
    });
    el.appendChild(grid);
  }

  /* A session's files, read-only, as three titled rows in a fixed order:
     Downloads, Screenshots, Files. Each is its own `fileGrid`, so paging
     between them opens the lightbox over that row's own list rather than a
     merge of all three. The admin dashboard draws its own rows instead, with
     actions, straight from `fileGrid` — this is what an MCP app renders,
     which holds no credential and offers none. */
  function fileSections(el, data, opts = {}) {
    el.innerHTML = '';
    const rows = [
      ['Downloads', data.downloads || [], 'No downloads.'],
      ['Screenshots', data.screenshots || [], 'No screenshots yet.'],
      ['Files', data.files || [], 'Nothing here yet — prints land here, and anything you keep.'],
    ];
    for (const [title, files, empty] of rows) {
      const row = document.createElement('section');
      row.className = 'row';
      row.innerHTML = '<div class="head"><strong>' + esc(title) + '</strong>' +
        '<span class="pill">' + files.length + '</span></div><div class="body"></div>';
      fileGrid(row.querySelector('.body'), files, {base: opts.base, empty});
      el.appendChild(row);
    }
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
        // for the others, where the heading is a kind rather than a name, and
        // the key is the only thing saying *which* one.
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

  /* Look at a file without leaving the page, stepping through the grid it came
     from rather than opening one at a time.

     `opts.action` — {label, run: async (file) => void} — is optional, same
     rule as the tile's: no credential, no button. After it resolves, the
     caller hands back the list to keep showing via `opts.refresh(index)` →
     {files, index}, so keeping a screenshot steps on to the next one instead
     of closing. `run` rejecting with 'cancelled' is not a failure — a confirm
     dismissed, say — so that one message is swallowed rather than alerted. */
  function lightbox(files, index, opts = {}) {
    const base = opts.base || '';
    const box = document.createElement('div');
    box.className = 'lightbox';
    function render() {
      const f = files[index];
      const href = base + f.url;
      const kind = f.content_type || '';
      const viewable = f.image || kind === 'application/pdf';
      box.innerHTML =
        '<div class="head"><span class="name">' + esc(f.name) + '</span>' +
        '<span class="pos">' + (index + 1) + ' / ' + files.length + '</span>' +
        '<span class="grow"></span>' +
        '<button type="button" data-prev' + (index === 0 ? ' disabled' : '') +
        '>‹ Prev</button>' +
        '<button type="button" data-next' +
        (index === files.length - 1 ? ' disabled' : '') + '>Next ›</button>' +
        (opts.action
          ? '<button type="button" data-act>' + esc(opts.action.label) + '</button>'
          : '') +
        '<a href="' + esc(href) + '" download="' + esc(f.name) + '">Download</a>' +
        '<button type="button" data-close>Close</button></div>' +
        '<div class="body">' +
        (f.image
          ? '<img alt="' + esc(f.name) + '" src="' + esc(href) + '">'
          : viewable
            ? '<iframe title="' + esc(f.name) + '" src="' + esc(href) + '"></iframe>'
            : '<div class="nopreview"><span class="glyph">' +
              (GLYPH[(f.name.split('.').pop() || '').toLowerCase()] || '📁') +
              '</span><p>No preview for this kind of file.</p>' +
              '<a href="' + esc(href) + '" download="' + esc(f.name) + '">Download</a></div>') +
        '</div>';
    }
    const step = (d) => {
      const n = index + d;
      if (n >= 0 && n < files.length) { index = n; render(); }
    };
    function close() {
      box.remove();
      document.removeEventListener('keydown', onKey);
      if (opts.onclose) opts.onclose();
    }
    function onKey(e) {
      if (e.key === 'Escape') close();
      else if (e.key === 'ArrowLeft') step(-1);
      else if (e.key === 'ArrowRight') step(1);
    }
    // Click the backdrop, Prev/Next, the action, the download link or Close.
    // Escape and the arrow keys do the same as the buttons; three ways to
    // leave, because a viewer you cannot dismiss is worse than a new tab.
    box.addEventListener('click', async (e) => {
      const t = e.target;
      if (t === box || t.hasAttribute('data-close')) return close();
      if (t.hasAttribute('data-prev')) return step(-1);
      if (t.hasAttribute('data-next')) return step(1);
      if (t.hasAttribute('data-act') && opts.action) {
        t.disabled = true;
        try {
          await opts.action.run(files[index]);
          const next = opts.refresh ? await opts.refresh(index) : null;
          if (!next || !next.files.length) return close();
          files = next.files;
          index = Math.min(next.index, files.length - 1);
          render();
        } catch (err) {
          t.disabled = false;
          if (err.message !== 'cancelled') alert(err.message);
        }
      }
    });
    document.addEventListener('keydown', onKey);
    render();
    document.body.appendChild(box);
  }

  return {sessionList, fileGrid, fileSections, sessionSummary, lightbox, bytes, ago, esc};
})();
