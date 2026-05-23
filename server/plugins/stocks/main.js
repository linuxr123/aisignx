(function () {
  'use strict';

  const cfgRaw = window.PLUGIN_CONFIG || {};
  const cfg = {
    symbols: String(cfgRaw.symbols || 'AAPL,MSFT,GOOGL,NVDA,TSLA'),
    apikey: String(cfgRaw.apikey || ''),
    theme: String(cfgRaw.theme || 'dark').toLowerCase(),
    refresh_seconds: Math.max(30, parseInt(cfgRaw.refresh_seconds || 60, 10)),
    scroll_speed: Math.max(20, parseInt(cfgRaw.scroll_speed || 50, 10)),
    loops: Math.max(0, parseInt(cfgRaw.loops || 0, 10)),
    direction: String(cfgRaw.direction || 'horizontal').toLowerCase()
  };


  const isDark = cfg.theme === 'dark' || (cfg.theme === 'auto' && window.matchMedia('(prefers-color-scheme: dark)').matches);

  const colors = {
    bg: isDark ? '#0a0a0a' : '#f5f5f7',
    text: isDark ? '#ffffff' : '#1d1d1f',
    textSecondary: isDark ? '#8e8e93' : '#6e6e73',
    green: '#30d158',
    red: '#ff453a',
    separator: isDark ? '#3a3a3c' : '#d1d1d6'
  };

  const root = document.getElementById('root') || document.body;
  document.documentElement.style.cssText = 'height:100%;margin:0;padding:0;';
  document.body.style.cssText = 'height:100%;margin:0;padding:0;background:' + colors.bg + ';font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica Neue,Arial,sans-serif;color:' + colors.text + ';overflow:hidden;';

  root.innerHTML = '<div id="ticker-wrapper"><div id="ticker-track"></div></div>';

  const st = document.createElement('style');
  const isVertical = cfg.direction === 'vertical';

  // Dynamic sizing for vertical mode based on viewport height
  let itemFontSize = 72;
  let symbolFontSize = 80;
  let nameFontSize = 56;
  let priceFontSize = 72;
  let changeFontSize = 64;
  let separatorFontSize = 60;
  let itemPadding = isVertical ? '80px 40px' : '0 80px';
  let itemGap = 32;

  if (isVertical) {
    const vh = window.innerHeight;
    const symbolCount = parseSymbols(cfg.symbols).length;

    // Calculate optimal size based on screen height and number of stocks
    // Target: fit at least 2-3 stocks visible at once for readability
    const targetStocksVisible = Math.min(3, symbolCount);
    const availableHeight = vh * 0.8; // Use 80% of viewport height
    const maxItemHeight = availableHeight / targetStocksVisible;

    // Scale font sizes proportionally to fit
    const scaleFactor = Math.min(1, maxItemHeight / 400); // 400px is baseline item height

    itemFontSize = Math.max(32, Math.floor(72 * scaleFactor));
    symbolFontSize = Math.max(36, Math.floor(80 * scaleFactor));
    nameFontSize = Math.max(28, Math.floor(56 * scaleFactor));
    priceFontSize = Math.max(32, Math.floor(72 * scaleFactor));
    changeFontSize = Math.max(28, Math.floor(64 * scaleFactor));
    separatorFontSize = Math.max(24, Math.floor(60 * scaleFactor));
    itemPadding = Math.max(20, Math.floor(80 * scaleFactor)) + 'px 40px';
    itemGap = Math.max(16, Math.floor(32 * scaleFactor));
  }

  const baseStyles = '*{box-sizing:border-box;margin:0;padding:0}';
  const wrapperStyles = '#ticker-wrapper{position:relative;width:100vw;height:100vh;display:flex;' + 
    (isVertical ? 'flex-direction:column;justify-content:flex-start' : 'align-items:center') + 
    ';overflow:hidden;background:' + colors.bg + '}';
  const trackStyles = '#ticker-track{display:' + (isVertical ? 'flex;flex-direction:column' : 'inline-flex') + 
    ';white-space:nowrap;will-change:transform}';
  const itemStyles = '.ticker-item{display:' + (isVertical ? 'flex' : 'inline-flex') + 
    ';align-items:center;gap:' + itemGap + 'px;padding:' + itemPadding + 
    ';font-size:' + itemFontSize + 'px;font-weight:600;letter-spacing:-1px' + (isVertical ? ';width:100%' : '') + '}';
  const symbolStyles = '.ticker-symbol{font-weight:800;color:' + colors.text + ';font-size:' + symbolFontSize + 'px}';
  const nameStyles = '.ticker-name{font-weight:500;color:' + colors.textSecondary + ';font-size:' + nameFontSize + 'px}';
  const priceStyles = '.ticker-price{font-weight:800;color:' + colors.text + ';font-size:' + priceFontSize + 'px}';
  const changeStyles = '.ticker-change{font-weight:700;font-size:' + changeFontSize + 'px}';
  const positiveStyles = '.positive{color:' + colors.green + '}';
  const negativeStyles = '.negative{color:' + colors.red + '}';
  const separatorStyles = '.ticker-separator{color:' + colors.separator + ';font-size:' + separatorFontSize + 'px;padding:' + 
    (isVertical ? '20px 0' : '0 50px') + (isVertical ? ';text-align:center;width:100%' : '') + '}';
  const scrollAnimation = isVertical ?
    '@keyframes scroll{0%{transform:translateY(0)}100%{transform:translateY(var(--scroll-distance))}}' :
    '@keyframes scroll{0%{transform:translateX(0)}100%{transform:translateX(var(--scroll-distance))}}';

  st.textContent = baseStyles + wrapperStyles + trackStyles + itemStyles + symbolStyles + nameStyles + 
    priceStyles + changeStyles + positiveStyles + negativeStyles + separatorStyles + scrollAnimation;
  document.head.appendChild(st);

  let stockData = [];

  function parseSymbols(str) {
    return String(str || '').split(',').map(s => s.trim().toUpperCase()).filter(Boolean);
  }

  async function fetchStockData(symbol) {
    if (!cfg.apikey) throw new Error('API key required');
    const quoteUrl = 'https://finnhub.io/api/v1/quote?symbol=' + symbol + '&token=' + cfg.apikey;
    const profileUrl = 'https://finnhub.io/api/v1/stock/profile2?symbol=' + symbol + '&token=' + cfg.apikey;
    try {
      const _fetch = window.cachedFetch || fetch;
      const [quoteRes, profileRes] = await Promise.all([
        _fetch(quoteUrl).then(r => r.json()),
        _fetch(profileUrl).then(r => r.json())
      ]);
      if (!quoteRes || quoteRes.c === undefined) throw new Error('Invalid data');
      return {
        symbol: symbol,
        name: profileRes.name || symbol,
        price: quoteRes.c,
        change: quoteRes.d,
        changePercent: quoteRes.dp
      };
    } catch (error) {
      console.error('Error fetching ' + symbol + ':', error);
      return null;
    }
  }

  function createTickerHTML() {
    if (stockData.length === 0) return '';

    let html = '';
    stockData.forEach(function(stock) {
      if (!stock) return;
      const isPositive = stock.change >= 0;
      const changeClass = isPositive ? 'positive' : 'negative';
      const arrow = isPositive ? '▲' : '▼';

      html += '<div class="ticker-item">';
      html += '<span class="ticker-symbol">' + escapeHtml(stock.symbol) + '</span>';
      html += '<span class="ticker-name">' + escapeHtml(stock.name) + '</span>';
      html += '<span class="ticker-price">$' + stock.price.toFixed(2) + '</span>';
      html += '<span class="ticker-change ' + changeClass + '">';
      html += arrow + ' ' + (isPositive ? '+' : '') + stock.change.toFixed(2);
      html += ' (' + (isPositive ? '+' : '') + stock.changePercent.toFixed(2) + '%)';
      html += '</span>';
      html += '</div>';
      html += '<span class="ticker-separator">•</span>';
    });

    return html;
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function startScrollAnimation() {
    const track = document.getElementById('ticker-track');
    const content = createTickerHTML();
    const isVertical = cfg.direction === 'vertical';

    track.innerHTML = content + content + content;

    const tempMeasure = document.createElement('div');
    tempMeasure.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap;display:' + 
      (isVertical ? 'flex;flex-direction:column' : 'inline-flex') + ';';
    tempMeasure.innerHTML = content;
    document.body.appendChild(tempMeasure);
    const size = isVertical ? tempMeasure.offsetHeight : tempMeasure.offsetWidth;
    document.body.removeChild(tempMeasure);

    const cycleDuration = size / cfg.scroll_speed;
    const iterationCount = cfg.loops === 0 ? 'infinite' : cfg.loops;

    track.style.cssText = '--scroll-distance:-' + size + 'px;';
    track.style.animation = 'none';
    track.offsetHeight;
    track.style.animation = 'scroll ' + cycleDuration + 's linear ' + iterationCount;
    track.style.animationFillMode = cfg.loops > 0 ? 'forwards' : 'none';

    // When a finite loop count finishes, signal the display player to advance
    if (cfg.loops > 0) {
      track.addEventListener('animationend', function onEnd() {
        track.removeEventListener('animationend', onEnd);
        try { window.parent.postMessage({ type: 'signage:complete' }, '*'); } catch {}
      });
    }
  }

  async function refreshData() {
    const symbols = parseSymbols(cfg.symbols);
    if (symbols.length === 0 || !cfg.apikey) {
      document.getElementById('ticker-track').innerHTML = '<div class="ticker-item" style="color:' + colors.red + '">⚠️ API Key Required - Get free key at finnhub.io/register</div>';
      return;
    }

    const cacheKey = 'stocks_cache_' + symbols.join(',');

    // Offline — use cache directly
    if (window.SIGNAGE_OFFLINE) {
      try {
        const cached = JSON.parse(localStorage.getItem(cacheKey) || 'null');
        if (cached && cached.data && cached.data.length) {
          stockData = cached.data;
          startScrollAnimation();
          return;
        }
      } catch {}
    }

    const results = await Promise.all(symbols.map(symbol => fetchStockData(symbol)));
    stockData = results.filter(r => r !== null);

    if (stockData.length > 0) {
      try { localStorage.setItem(cacheKey, JSON.stringify({ t: Date.now(), data: stockData })); } catch {}
      startScrollAnimation();
    } else {
      // Try cache as fallback
      try {
        const cached = JSON.parse(localStorage.getItem(cacheKey) || 'null');
        if (cached && cached.data && cached.data.length) {
          stockData = cached.data;
          startScrollAnimation();
          return;
        }
      } catch {}
      document.getElementById('ticker-track').innerHTML = '';
    }
  }

  refreshData();
  setInterval(refreshData, cfg.refresh_seconds * 1000);
  window.addEventListener('signage:online_changed', () => refreshData());

  console.log('[Stock Ticker] ' + parseSymbols(cfg.symbols).length + ' symbols | Direction: ' + cfg.direction + ' | Speed: ' + cfg.scroll_speed + 'px/s | Loops: ' + (cfg.loops === 0 ? 'infinite' : cfg.loops) + ' | Refresh: ' + cfg.refresh_seconds + 's');
})();
