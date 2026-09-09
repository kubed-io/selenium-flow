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
     opts.onpick — called with a session id when a row is chosen. Omit it and
     the rows render as plain, non-interactive summaries, which is what an app
     embedded in a transcript wants. */
  function sessionList(el, data, opts = {}) {
    const sessions = (data && data.sessions) || [];
    if (!sessions.length) return empty(el, 'No browsers are running.');
    el.innerHTML = '';
    for (const s of sessions) {
      const card = document.createElement('div');
      card.className = 'card' + (opts.onpick ? ' click' : '');
      const count = s.files_count;
      card.innerHTML =
        '<div class="row"><span class="mono grow">' + esc(s.session_id) + '</span>' +
        '<span class="pill live">live</span></div>' +
        '<div class="small muted" style="margin-top:6px">' +
        esc([s.browser, s.version].filter(Boolean).join(' ')) +
        (count === undefined ? '' : ' · ' + count + ' file' + (count === 1 ? '' : 's')) +
        (s.node ? ' · ' + esc(s.node) : '') + '</div>';
      if (opts.onpick) card.onclick = () => opts.onpick(s.session_id);
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
      grid.appendChild(item);
    }
    el.appendChild(grid);
  }

  /* One session's headline: what it is and where it is. */
  function sessionSummary(el, data) {
    const s = data || {};
    el.innerHTML =
      '<div class="card"><div class="row">' +
      '<span class="mono grow">' + esc(s.session_id || 'no session') + '</span>' +
      (s.live === false ? '<span class="pill">ended</span>' : '<span class="pill live">live</span>') +
      '</div>' +
      (s.url ? '<div class="small muted" style="margin-top:6px">' + esc(s.url) + '</div>' : '') +
      '</div>';
  }

  return {sessionList, fileGrid, sessionSummary, bytes, ago, esc};
})();
