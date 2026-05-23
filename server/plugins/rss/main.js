(function () {
  const cfg = window.PLUGIN_CONFIG || {};
  const root = document.getElementById('root');

  const theme = (cfg.theme || 'dark').toLowerCase();
  const bg = theme === 'light' ? '#ffffff' : '#000000';
  const fg = theme === 'light' ? '#000000' : '#ffffff';
  const sub = theme === 'light' ? '#333333' : '#cccccc';
  root.style.background = bg;
  root.style.color = fg;
  root.style.display = 'flex';
  root.style.flexDirection = 'column';
  root.style.padding = '2vmin';
  root.style.boxSizing = 'border-box';

  const header = document.createElement('div');
  header.textContent = 'News';
  header.style.fontSize = '5vmin';
  header.style.fontWeight = '700';
  header.style.marginBottom = '1vmin';

  const list = document.createElement('div');
  list.style.display = 'flex';
  list.style.flexDirection = 'column';
  list.style.gap = '1.2vmin';
  list.style.overflow = 'hidden';
  list.style.flex = '1 1 auto';

  root.innerHTML = '';
  root.appendChild(header);
  root.appendChild(list);

  function itemEl(title, pubDate, link) {
    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.flexDirection = 'column';
    const t = document.createElement('div');
    t.textContent = title || '';
    t.style.fontSize = '3.5vmin';
    t.style.fontWeight = '600';
    const d = document.createElement('div');
    d.textContent = pubDate || '';
    d.style.fontSize = '2.5vmin';
    d.style.color = sub;
    row.appendChild(t);
    row.appendChild(d);
    row.addEventListener('click', () => {
      try { window.location.href = link; } catch(e) {}
    });
    row.style.cursor = 'pointer';
    return row;
  }

  async function fetchRSS(url) {
    // rss2json free endpoint (rate limits may apply). For production, proxy via server.
    // cachedFetch transparently falls back to the last cached response when
    // the network is unreachable so the plugin keeps showing headlines
    // while the kiosk is offline. Falls back to plain fetch on older players
    // that haven't picked up the helper yet.
    const api = `https://api.rss2json.com/v1/api.json?rss_url=${encodeURIComponent(url)}`;
    const _fetch = window.cachedFetch || fetch;
    const res = await _fetch(api);
    return res.json();
  }

  function fmtDate(iso) {
    try {
      const d = new Date(iso);
      return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(d);
    } catch { return ''; }
  }

  async function render() {
    list.innerHTML = '';
    const feed = (cfg.rss_url || '').trim();
    if (!feed) {
      const msg = document.createElement('div');
      msg.textContent = 'Set RSS URL in plugin settings';
      msg.style.fontSize = '3vmin';
      msg.style.opacity = '0.8';
      list.appendChild(msg);
      return;
    }

    const cacheKey = 'rss_cache_' + feed;
    const renderItems = (items) => {
      list.innerHTML = '';
      const max = Math.max(1, parseInt(cfg.max_items || 8, 10));
      const sliced = items.slice(0, max);
      if (!sliced.length) return;
      for (const it of sliced) {
        list.appendChild(itemEl(it.title, fmtDate(it.pubDate || it.pubdate || it.date || ''), it.link));
      }
    };

    // Offline — use cache directly
    if (window.SIGNAGE_OFFLINE) {
      try {
        const cached = JSON.parse(localStorage.getItem(cacheKey) || 'null');
        if (cached && cached.items) { renderItems(cached.items); return; }
      } catch {}
    }

    try {
      const data = await fetchRSS(feed);
      const items = data.items || [];
      try { localStorage.setItem(cacheKey, JSON.stringify({ t: Date.now(), items })); } catch {}
      renderItems(items);
    } catch (e) {
      // Network failed — fall back to cache
      try {
        const cached = JSON.parse(localStorage.getItem(cacheKey) || 'null');
        if (cached && cached.items) { renderItems(cached.items); return; }
      } catch {}
      const msg = document.createElement('div');
      msg.textContent = 'Failed to load RSS feed';
      msg.style.fontSize = '3vmin';
      msg.style.opacity = '0.8';
      list.appendChild(msg);
    }
  }

  render();
  const secs = Math.max(30, parseInt(cfg.refresh_seconds || 300, 10));
  setInterval(render, secs * 1000);
  window.addEventListener('signage:online_changed', () => render());

  // Advance to next playlist item after duration, unless looping
  const shouldLoop = !!(window.PLUGIN_CONFIG || {}).loop;
  const duration   = Math.max(1, parseInt((window.PLUGIN_CONFIG || {}).duration || 30, 10));
  if (!shouldLoop) {
    setTimeout(() => {
      try { window.parent.postMessage({ type: 'signage:complete' }, '*'); } catch {}
    }, duration * 1000);
  }
})();