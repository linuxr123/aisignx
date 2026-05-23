(function () {
  console.log('[Weather] plugin loaded - build 2026-04-25d today-uses-current');
  const cfgRaw = window.PLUGIN_CONFIG || {};
  const cfg = {
    units: String(cfgRaw.units || 'imperial').toLowerCase(),      // 'metric' | 'imperial'
    location: String(cfgRaw.location || '38.6487,-78.6745'),      // "City, Country" OR "lat,lon"
    locationLabel: cfgRaw.locationLabel ? String(cfgRaw.locationLabel) : '',  // Display label (optional - will use geocoded name if not provided)
    refresh_seconds: Math.max(30, parseInt(cfgRaw.refresh_seconds || 600, 10)),
    language: String(cfgRaw.language || 'en'),
    // Optional proxy (only needed if direct NWS API access is blocked by your network)
    proxy_base: cfgRaw.proxy_base || '',
    use_proxy: !!cfgRaw.use_proxy  // Set to true only if direct access fails
  };

  // Root setup - TV-style layout
  let root = document.getElementById('root');
  if (!root) {
    root = document.createElement('div');
    root.id = 'root';
    document.body.appendChild(root);
  }
  root.style.display = 'grid';
  root.style.gridTemplateRows = 'auto 1fr auto';
  root.style.gridTemplateColumns = '1fr';
  root.style.width = '100%';
  root.style.height = '100%';
  root.style.padding = '2vmin';
  root.style.boxSizing = 'border-box';
  root.style.gap = '2vmin';
  root.style.overflow = 'hidden';

  // Top bar - Date, Time, Location
  const topBar = document.createElement('div');
  topBar.style.display = 'flex';
  topBar.style.justifyContent = 'space-between';
  topBar.style.alignItems = 'center';
  topBar.style.padding = '1.5vmin 2vmin';
  topBar.style.backgroundColor = 'rgba(255, 255, 255, 0.08)';
  topBar.style.borderRadius = '1vmin';

  const dateTimeContainer = document.createElement('div');
  dateTimeContainer.style.display = 'flex';
  dateTimeContainer.style.flexDirection = 'column';
  dateTimeContainer.style.alignItems = 'flex-start';

  const dateEl = document.createElement('div');
  const timeEl = document.createElement('div');
  dateEl.style.fontSize = '2.5vmin';
  dateEl.style.opacity = '0.85';
  timeEl.style.fontSize = '5vmin';
  timeEl.style.fontWeight = '700';
  timeEl.style.letterSpacing = '0.05em';
  dateTimeContainer.appendChild(timeEl);
  dateTimeContainer.appendChild(dateEl);

  const placeEl = document.createElement('div');
  placeEl.style.fontSize = '3.5vmin';
  placeEl.style.fontWeight = '600';
  placeEl.style.opacity = '0.9';

  topBar.appendChild(dateTimeContainer);
  topBar.appendChild(placeEl);

  // Main content area - Current weather (left) + 7-day forecast (right)
  const mainContent = document.createElement('div');
  mainContent.style.display = 'grid';
  mainContent.style.gridTemplateColumns = '45% 55%';
  mainContent.style.gap = '2vmin';
  mainContent.style.height = '100%';
  mainContent.style.overflow = 'hidden';

  // Left side - Current weather (hero display)
  const currentContainer = document.createElement('div');
  currentContainer.style.display = 'flex';
  currentContainer.style.flexDirection = 'column';
  currentContainer.style.alignItems = 'center';
  currentContainer.style.justifyContent = 'center';
  currentContainer.style.backgroundColor = 'rgba(255, 255, 255, 0.08)';
  currentContainer.style.borderRadius = '1.5vmin';
  currentContainer.style.padding = '2vmin';
  currentContainer.style.minHeight = '0';
  currentContainer.style.overflow = 'hidden';
  currentContainer.style.boxSizing = 'border-box';

  const weatherIconEl = document.createElement('div');
  const tempEl = document.createElement('div');
  const descEl = document.createElement('div');

  // Additional current weather stats grid
  const statsGrid = document.createElement('div');
  statsGrid.style.display = 'flex';
  statsGrid.style.flexWrap = 'wrap';
  statsGrid.style.justifyContent = 'center';
  statsGrid.style.gap = '1.2vmin';
  statsGrid.style.marginTop = '1.5vmin';
  statsGrid.style.width = '100%';
  statsGrid.style.maxWidth = '90%';
  statsGrid.style.margin = '1.5vmin auto 0 auto';

  weatherIconEl.style.width = '22vmin';
  weatherIconEl.style.height = '22vmin';
  weatherIconEl.style.marginBottom = '1vmin';
  tempEl.style.fontSize = '14vmin';
  tempEl.style.fontWeight = '700';
  tempEl.style.lineHeight = '1';
  descEl.style.fontSize = '3.5vmin';
  descEl.style.marginTop = '1vmin';
  descEl.style.opacity = '0.9';

  currentContainer.appendChild(weatherIconEl);
  currentContainer.appendChild(tempEl);
  currentContainer.appendChild(descEl);
  currentContainer.appendChild(statsGrid);

  // Right side - 7-day forecast grid
  const forecastSection = document.createElement('div');
  forecastSection.style.display = 'flex';
  forecastSection.style.flexDirection = 'column';
  forecastSection.style.backgroundColor = 'rgba(255, 255, 255, 0.08)';
  forecastSection.style.borderRadius = '1.5vmin';
  forecastSection.style.padding = '2vmin';
  forecastSection.style.height = '100%';
  forecastSection.style.overflow = 'hidden';
  forecastSection.style.boxSizing = 'border-box';

  const forecastTitle = document.createElement('div');
  forecastTitle.textContent = '7-DAY FORECAST';
  forecastTitle.style.fontSize = '3vmin';
  forecastTitle.style.fontWeight = '700';
  forecastTitle.style.marginBottom = '1.5vmin';
  forecastTitle.style.opacity = '0.9';
  forecastTitle.style.letterSpacing = '0.1em';

  const forecastContainer = document.createElement('div');
  forecastContainer.style.display = 'grid';
  forecastContainer.style.gridTemplateColumns = 'repeat(1, 1fr)';
  forecastContainer.style.gap = '1.2vmin';
  forecastContainer.style.flex = '1';
  forecastContainer.style.overflow = 'auto';
  forecastContainer.style.paddingBottom = '0.5vmin';

  forecastSection.appendChild(forecastTitle);
  forecastSection.appendChild(forecastContainer);

  mainContent.appendChild(currentContainer);
  mainContent.appendChild(forecastSection);

  // Footer for error messages and debug info
  const footEl = document.createElement('div');
  footEl.style.fontSize = '2vmin';
  footEl.style.opacity = '0.7';
  footEl.style.textAlign = 'center';
  footEl.style.minHeight = '2vmin';

  // Debug overlay (can be toggled with 'D' key)
  const debugEl = document.createElement('div');
  debugEl.style.position = 'fixed';
  debugEl.style.bottom = '1vmin';
  debugEl.style.right = '1vmin';
  debugEl.style.backgroundColor = 'rgba(0, 0, 0, 0.85)';
  debugEl.style.color = '#00ff00';
  debugEl.style.padding = '1vmin';
  debugEl.style.borderRadius = '0.5vmin';
  debugEl.style.fontSize = '1.5vmin';
  debugEl.style.fontFamily = 'monospace';
  debugEl.style.maxWidth = '40vmin';
  debugEl.style.maxHeight = '50vh';
  debugEl.style.overflow = 'auto';
  debugEl.style.display = 'none';
  debugEl.style.zIndex = '9999';
  document.body.appendChild(debugEl);

  // Toggle debug with 'D' key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'd' || e.key === 'D') {
      debugEl.style.display = debugEl.style.display === 'none' ? 'block' : 'none';
    }
  });

  root.innerHTML = '';
  root.appendChild(topBar);
  root.appendChild(mainContent);
  root.appendChild(footEl);

  // Add weather icon styles
  if (!document.getElementById('weather-icon-styles')) {
    const iconStyles = document.createElement('style');
    iconStyles.id = 'weather-icon-styles';
    iconStyles.textContent = `
      .wx-icon {
        display: inline-block;
        line-height: 1;
        width: 100%;
        height: 100%;
      }
      .wx-icon svg {
        width: 100%;
        height: 100%;
        display: block;
      }
      .wx-sun { filter: drop-shadow(0 0 10px rgba(255, 215, 0, 0.5)); }
      .wx-mostly-clear { filter: drop-shadow(0 0 8px rgba(135, 206, 235, 0.4)); }
      .wx-cloudy, .wx-overcast { filter: drop-shadow(0 0 5px rgba(176, 176, 176, 0.3)); }
      .wx-fog { filter: drop-shadow(0 0 10px rgba(160, 160, 160, 0.6)); }
      .wx-drizzle { filter: drop-shadow(0 0 6px rgba(137, 207, 240, 0.4)); }
      .wx-light-rain { filter: drop-shadow(0 0 6px rgba(70, 130, 180, 0.4)); }
      .wx-rain { filter: drop-shadow(0 0 8px rgba(65, 105, 225, 0.5)); }
      .wx-heavy-rain { filter: drop-shadow(0 0 8px rgba(0, 0, 139, 0.6)); }
      .wx-showers { filter: drop-shadow(0 0 8px rgba(30, 144, 255, 0.5)); }
      .wx-snow { filter: drop-shadow(0 0 10px rgba(224, 255, 255, 0.8)); }
      .wx-snow-heavy { filter: drop-shadow(0 0 8px rgba(176, 224, 230, 0.6)); }
      .wx-storm { filter: drop-shadow(0 0 12px rgba(255, 215, 0, 0.8)); }
      .wx-unknown { opacity: 0.6; }
    `;
    document.head.appendChild(iconStyles);
  }

  const WMO_CODES = {
    0: 'Clear sky', 1: 'Mainly clear', 2: 'Partly cloudy', 3: 'Overcast',
    45: 'Fog', 48: 'Depositing rime fog',
    51: 'Drizzle: light', 53: 'Drizzle: moderate', 55: 'Drizzle: dense',
    56: 'Freezing drizzle: light', 57: 'Freezing drizzle: dense',
    61: 'Rain: slight', 63: 'Rain: moderate', 65: 'Rain: heavy',
    66: 'Freezing rain: light', 67: 'Freezing rain: heavy',
    71: 'Snow fall: slight', 73: 'Snow fall: moderate', 75: 'Snow fall: heavy',
    77: 'Snow grains',
    80: 'Rain showers: slight', 81: 'Rain showers: moderate', 82: 'Rain showers: violent',
    85: 'Snow showers: slight', 86: 'Snow showers: heavy',
    95: 'Thunderstorm: slight/moderate', 96: 'Thunderstorm with hail: slight', 99: 'Thunderstorm with hail: heavy'
  };

  // Map WMO codes to weather icons (using inline SVG for cross-platform compatibility).
  // The optional isDay flag swaps in moon/star variants for clear-sky codes
  // (0 = clear, 1 = mostly clear, 2 = partly cloudy). All other codes look
  // identical regardless of time of day.
  function getWeatherIcon(code, isDay) {
    if (code == null || code === undefined) {
      return '<div class="wx-icon wx-unknown"><svg viewBox="0 0 100 100"><text x="50" y="70" font-size="60" text-anchor="middle" fill="#808080">?</text></svg></div>';
    }

    const c = parseInt(code, 10);
    // Default to day if caller didn't specify, so existing call sites that
    // haven't been updated still get the old behaviour.
    const day = (isDay == null) ? true : !!isDay;

    // Clear sky - Sun (day) / Moon with stars (night)
    if (c === 0) {
      if (day) {
        return '<div class="wx-icon wx-sun"><svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="20" fill="#FFD700"/><g stroke="#FFD700" stroke-width="3" stroke-linecap="round"><line x1="50" y1="10" x2="50" y2="22"/><line x1="50" y1="78" x2="50" y2="90"/><line x1="10" y1="50" x2="22" y2="50"/><line x1="78" y1="50" x2="90" y2="50"/><line x1="21" y1="21" x2="30" y2="30"/><line x1="70" y1="70" x2="79" y2="79"/><line x1="79" y1="21" x2="70" y2="30"/><line x1="30" y1="70" x2="21" y2="79"/></g></svg></div>';
      }
      // Night: crescent moon with a few stars
      return '<div class="wx-icon wx-moon"><svg viewBox="0 0 100 100"><path d="M62,18 a32,32 0 1,0 22,55 a26,26 0 1,1 -22,-55 z" fill="#F4F4D8"/><g fill="#FFFFFF"><circle cx="20" cy="25" r="1.6"/><circle cx="30" cy="60" r="1.4"/><circle cx="18" cy="78" r="1.8"/><circle cx="80" cy="20" r="1.2"/></g></svg></div>';
    }

    // Mainly clear - Sun + small cloud (day) / Moon + small cloud (night)
    if (c === 1) {
      if (day) {
        return '<div class="wx-icon wx-mostly-clear"><svg viewBox="0 0 100 100"><circle cx="35" cy="35" r="15" fill="#FFD700"/><g stroke="#FFD700" stroke-width="2.5" stroke-linecap="round"><line x1="35" y1="10" x2="35" y2="18"/><line x1="35" y1="52" x2="35" y2="60"/><line x1="10" y1="35" x2="18" y2="35"/><line x1="52" y1="35" x2="60" y2="35"/><line x1="18" y1="18" x2="24" y2="24"/><line x1="46" y1="46" x2="52" y2="52"/><line x1="52" y1="18" x2="46" y2="24"/><line x1="24" y1="46" x2="18" y2="52"/></g><ellipse cx="65" cy="60" rx="20" ry="15" fill="#E0E0E0"/><ellipse cx="75" cy="63" rx="15" ry="12" fill="#F0F0F0"/><ellipse cx="55" cy="63" rx="12" ry="10" fill="#D0D0D0"/></svg></div>';
      }
      return '<div class="wx-icon wx-mostly-clear-night"><svg viewBox="0 0 100 100"><path d="M40,18 a22,22 0 1,0 16,38 a18,18 0 1,1 -16,-38 z" fill="#F4F4D8"/><ellipse cx="65" cy="65" rx="22" ry="16" fill="#7090B0"/><ellipse cx="78" cy="68" rx="15" ry="12" fill="#8AA4C4"/><ellipse cx="55" cy="68" rx="13" ry="10" fill="#5A7898"/></svg></div>';
    }

    // Partly cloudy - Cloud with some sun peeking (day) / cloud with moon (night)
    if (c === 2) {
      if (day) {
        return '<div class="wx-icon wx-cloudy"><svg viewBox="0 0 100 100"><ellipse cx="40" cy="45" rx="25" ry="18" fill="#B0B0B0"/><ellipse cx="55" cy="48" rx="22" ry="16" fill="#C8C8C8"/><ellipse cx="25" cy="48" rx="18" ry="14" fill="#A0A0A0"/><ellipse cx="40" cy="55" rx="28" ry="20" fill="#D0D0D0"/></svg></div>';
      }
      return '<div class="wx-icon wx-partly-cloudy-night"><svg viewBox="0 0 100 100"><path d="M30,20 a18,18 0 1,0 14,30 a14,14 0 1,1 -14,-30 z" fill="#F4F4D8"/><ellipse cx="55" cy="60" rx="28" ry="20" fill="#5A6A7A"/><ellipse cx="72" cy="63" rx="20" ry="15" fill="#6A7A8A"/><ellipse cx="38" cy="63" rx="18" ry="14" fill="#4A5A6A"/></svg></div>';
    }

    // Overcast - Dark cloud
    if (c === 3) {
      return '<div class="wx-icon wx-overcast"><svg viewBox="0 0 100 100"><ellipse cx="40" cy="40" rx="28" ry="20" fill="#808080"/><ellipse cx="58" cy="43" rx="25" ry="18" fill="#909090"/><ellipse cx="22" cy="43" rx="20" ry="16" fill="#707070"/><ellipse cx="40" cy="52" rx="32" ry="22" fill="#A0A0A0"/></svg></div>';
    }

    // Fog - Horizontal lines
    if (c === 45 || c === 48) {
      return '<div class="wx-icon wx-fog"><svg viewBox="0 0 100 100"><g stroke="#A0A0A0" stroke-width="6" stroke-linecap="round"><line x1="15" y1="25" x2="85" y2="25"/><line x1="20" y1="40" x2="80" y2="40"/><line x1="15" y1="55" x2="85" y2="55"/><line x1="20" y1="70" x2="80" y2="70"/></g></svg></div>';
    }

    // Drizzle - Light rain drops
    if (c >= 51 && c <= 55) {
      return '<div class="wx-icon wx-drizzle"><svg viewBox="0 0 100 100"><ellipse cx="40" cy="30" rx="22" ry="16" fill="#89CFF0"/><ellipse cx="55" cy="33" rx="18" ry="14" fill="#A0D8F0"/><ellipse cx="25" cy="33" rx="15" ry="12" fill="#70C0E0"/><g stroke="#89CFF0" stroke-width="2" stroke-linecap="round"><line x1="30" y1="50" x2="28" y2="65"/><line x1="45" y1="50" x2="43" y2="65"/><line x1="60" y1="50" x2="58" y2="65"/></g></svg></div>';
    }

    // Freezing drizzle
    if (c === 56 || c === 57) {
      return '<div class="wx-icon wx-rain"><svg viewBox="0 0 100 100"><ellipse cx="40" cy="30" rx="22" ry="16" fill="#4169E1"/><ellipse cx="55" cy="33" rx="18" ry="14" fill="#6A8FE8"/><ellipse cx="25" cy="33" rx="15" ry="12" fill="#2050D0"/><g stroke="#4169E1" stroke-width="2.5" stroke-linecap="round"><line x1="28" y1="50" x2="26" y2="68"/><line x1="43" y1="50" x2="41" y2="68"/><line x1="58" y1="50" x2="56" y2="68"/><line x1="35" y1="55" x2="33" y2="73"/><line x1="50" y1="55" x2="48" y2="73"/></g></svg></div>';
    }

    // Light rain
    if (c === 61) {
      return '<div class="wx-icon wx-light-rain"><svg viewBox="0 0 100 100"><ellipse cx="40" cy="30" rx="22" ry="16" fill="#4682B4"/><ellipse cx="55" cy="33" rx="18" ry="14" fill="#5A96C8"/><ellipse cx="25" cy="33" rx="15" ry="12" fill="#3070A0"/><g stroke="#4682B4" stroke-width="2.5" stroke-linecap="round"><line x1="25" y1="50" x2="22" y2="70"/><line x1="40" y1="50" x2="37" y2="70"/><line x1="55" y1="50" x2="52" y2="70"/><line x1="32" y1="55" x2="29" y2="75"/><line x1="47" y1="55" x2="44" y2="75"/></g></svg></div>';
    }

    // Moderate rain
    if (c === 63) {
      return '<div class="wx-icon wx-rain"><svg viewBox="0 0 100 100"><ellipse cx="40" cy="30" rx="22" ry="16" fill="#4169E1"/><ellipse cx="55" cy="33" rx="18" ry="14" fill="#6A8FE8"/><ellipse cx="25" cy="33" rx="15" ry="12" fill="#2050D0"/><g stroke="#4169E1" stroke-width="3" stroke-linecap="round"><line x1="23" y1="50" x2="20" y2="72"/><line x1="35" y1="50" x2="32" y2="72"/><line x1="47" y1="50" x2="44" y2="72"/><line x1="59" y1="50" x2="56" y2="72"/><line x1="29" y1="55" x2="26" y2="77"/><line x1="41" y1="55" x2="38" y2="77"/><line x1="53" y1="55" x2="50" y2="77"/></g></svg></div>';
    }

    // Heavy rain
    if (c === 65 || c === 66 || c === 67) {
      return '<div class="wx-icon wx-heavy-rain"><svg viewBox="0 0 100 100"><ellipse cx="40" cy="28" rx="24" ry="18" fill="#00008B"/><ellipse cx="58" cy="31" rx="20" ry="16" fill="#1010A0"/><ellipse cx="22" cy="31" rx="17" ry="14" fill="#000070"/><g stroke="#00008B" stroke-width="3.5" stroke-linecap="round"><line x1="20" y1="50" x2="17" y2="75"/><line x1="30" y1="50" x2="27" y2="75"/><line x1="40" y1="50" x2="37" y2="75"/><line x1="50" y1="50" x2="47" y2="75"/><line x1="60" y1="50" x2="57" y2="75"/><line x1="25" y1="56" x2="22" y2="81"/><line x1="35" y1="56" x2="32" y2="81"/><line x1="45" y1="56" x2="42" y2="81"/><line x1="55" y1="56" x2="52" y2="81"/></g></svg></div>';
    }

    // Snow
    if (c >= 71 && c <= 75 || c === 77) {
      return '<div class="wx-icon wx-snow"><svg viewBox="0 0 100 100"><ellipse cx="40" cy="30" rx="22" ry="16" fill="#E0FFFF"/><ellipse cx="55" cy="33" rx="18" ry="14" fill="#F0FFFF"/><ellipse cx="25" cy="33" rx="15" ry="12" fill="#D0F0F0"/><g fill="#E0FFFF"><path d="M30,55 l3,-8 l3,8 l8,3 l-8,3 l-3,8 l-3,-8 l-8,-3 z"/><path d="M50,55 l3,-8 l3,8 l8,3 l-8,3 l-3,8 l-3,-8 l-8,-3 z"/><path d="M40,68 l2.5,-6.5 l2.5,6.5 l6.5,2.5 l-6.5,2.5 l-2.5,6.5 l-2.5,-6.5 l-6.5,-2.5 z"/></g></svg></div>';
    }

    // Rain showers
    if (c >= 80 && c <= 82) {
      return '<div class="wx-icon wx-showers"><svg viewBox="0 0 100 100"><ellipse cx="40" cy="28" rx="24" ry="18" fill="#1E90FF"/><ellipse cx="58" cy="31" rx="20" ry="16" fill="#3AA0FF"/><ellipse cx="22" cy="31" rx="17" ry="14" fill="#0080E0"/><g stroke="#1E90FF" stroke-width="3.5" stroke-linecap="round"><line x1="25" y1="52" x2="22" y2="72"/><line x1="38" y1="52" x2="35" y2="72"/><line x1="51" y1="52" x2="48" y2="72"/><line x1="31" y1="58" x2="28" y2="78"/><line x1="44" y1="58" x2="41" y2="78"/></g></svg></div>';
    }

    // Snow showers
    if (c === 85 || c === 86) {
      return '<div class="wx-icon wx-snow-heavy"><svg viewBox="0 0 100 100"><ellipse cx="40" cy="25" rx="24" ry="18" fill="#B0E0E6"/><ellipse cx="58" cy="28" rx="20" ry="16" fill="#C8EFF6"/><ellipse cx="22" cy="28" rx="17" ry="14" fill="#98D0D6"/><g fill="#B0E0E6"><path d="M25,50 l3,-8 l3,8 l8,3 l-8,3 l-3,8 l-3,-8 l-8,-3 z"/><path d="M45,50 l3,-8 l3,8 l8,3 l-8,3 l-3,8 l-3,-8 l-8,-3 z"/><path d="M35,65 l3,-8 l3,8 l8,3 l-8,3 l-3,8 l-3,-8 l-8,-3 z"/><path d="M55,65 l2.5,-6.5 l2.5,6.5 l6.5,2.5 l-6.5,2.5 l-2.5,6.5 l-2.5,-6.5 l-6.5,-2.5 z"/></g></svg></div>';
    }

    // Thunderstorm - Cloud with lightning
    if (c >= 95 && c <= 99) {
      return '<div class="wx-icon wx-storm"><svg viewBox="0 0 100 100"><ellipse cx="40" cy="25" rx="24" ry="18" fill="#4A4A4A"/><ellipse cx="58" cy="28" rx="20" ry="16" fill="#5A5A5A"/><ellipse cx="22" cy="28" rx="17" ry="14" fill="#3A3A3A"/><path d="M50,45 L42,60 L48,60 L40,80 L55,62 L49,62 L57,45 Z" fill="#FFD700" stroke="#FFA500" stroke-width="1"/></svg></div>';
    }

    console.warn('[Weather] Unknown weather code:', c);
    return '<div class="wx-icon wx-unknown"><svg viewBox="0 0 100 100"><text x="50" y="70" font-size="60" text-anchor="middle" fill="#808080">?</text></svg></div>';
  }

  // Check if it's currently daytime or nighttime
  function isDaytime() {
    const now = new Date();
    const hour = now.getHours();
    return hour >= 6 && hour < 20; // Daytime is 6 AM to 8 PM
  }

  // Get animated background gradient based on weather code and time of day
  function getWeatherBackground(code) {
    if (code == null || code === undefined) {
      return 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)';
    }

    const c = parseInt(code, 10);
    const isDay = isDaytime();  // Renamed to avoid shadowing

    // Clear sky - Bright blue sunny sky (day) or dark starry night
    if (c === 0) {
      return isDay 
        ? 'linear-gradient(to bottom, #87CEEB 0%, #E0F6FF 50%, #FFE5B4 100%)'
        : 'linear-gradient(to bottom, #0a1929 0%, #1a2332 50%, #2a3f5f 100%)';
    }

    // Mainly clear - Light blue with some clouds (day) or dark with stars (night)
    if (c === 1) {
      return isDay
        ? 'linear-gradient(135deg, #667eea 0%, #89CFF0 50%, #B4D4FF 100%)'
        : 'linear-gradient(135deg, #1a2332 0%, #2a3f5f 50%, #3a4f6f 100%)';
    }

    // Partly cloudy - Mixed blue and gray
    if (c === 2) {
      return 'linear-gradient(to bottom, #8E9EAB 0%, #B8C6DB 50%, #E5E5E5 100%)';
    }

    // Overcast - Gray sky
    if (c === 3) {
      return 'linear-gradient(to bottom, #757F9A 0%, #949DAA 50%, #B0B8C5 100%)';
    }

    // Fog - Misty gray
    if (c === 45 || c === 48) {
      return 'linear-gradient(135deg, #9BA5B0 0%, #C7CDD4 50%, #D8DDE4 100%)';
    }

    // Drizzle/Light rain - Soft blue-gray
    if (c >= 51 && c <= 55 || c === 61) {
      return 'linear-gradient(to bottom, #4A5568 0%, #6B7994 50%, #8B99AB 100%)';
    }

    // Moderate to heavy rain - Dark blue-gray
    if (c === 56 || c === 57 || c === 63 || c === 65 || c === 66 || c === 67 || (c >= 80 && c <= 82)) {
      return 'linear-gradient(to bottom, #2C3E50 0%, #4A5F7F 50%, #5D7394 100%)';
    }

    // Snow - White and light blue
    if (c >= 71 && c <= 77 || c === 85 || c === 86) {
      return 'linear-gradient(135deg, #E0E5EC 0%, #D0D8E8 50%, #B8C5DC 100%)';
    }

    // Thunderstorm - Dark dramatic sky
    if (c >= 95 && c <= 99) {
      return 'linear-gradient(to bottom, #1a1a2e 0%, #2d3561 50%, #4a5a7f 100%)';
    }

    // Default
    return 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)';
  }

  // Determine if weather background is light (needs dark text) or dark (needs light text)
  function isLightBackground(code) {
    if (code == null) return false;
    const c = parseInt(code, 10);
    const isDay = isDaytime();

    // Clear/mainly clear weather changes based on time of day
    if (c === 0 || c === 1) {
      return isDay; // Light during day, dark at night
    }

    // Always light backgrounds: partly cloudy, fog, snow
    if (c === 2 || (c >= 45 && c <= 48) || (c >= 71 && c <= 77) || c === 85 || c === 86) {
      return true;
    }

    return false; // Dark backgrounds for rain, storms, etc.
  }

  // Apply background with animation and adjust text colors for contrast
  function setWeatherBackground(code) {
    const gradient = getWeatherBackground(code);
    const isLight = isLightBackground(code);

    document.body.style.background = gradient;
    document.body.style.backgroundSize = '400% 400%';
    document.body.style.animation = 'gradientShift 15s ease infinite';

    // Set text color based on background brightness
    const textColor = isLight ? '#000000' : '#ffffff';
    const shadowColor = isLight ? 'rgba(255, 255, 255, 0.9)' : 'rgba(0, 0, 0, 0.9)';

    // Update all text elements
    document.body.style.color = textColor;
    root.style.color = textColor;

    // Add strong text shadow for better readability
    const textShadow = `0 2px 4px ${shadowColor}, 0 0 10px ${shadowColor}`;
    timeEl.style.textShadow = textShadow;
    dateEl.style.textShadow = textShadow;
    placeEl.style.textShadow = textShadow;
    tempEl.style.textShadow = textShadow;
    descEl.style.textShadow = textShadow;
    forecastTitle.style.textShadow = textShadow;

    // Adjust panel backgrounds for better contrast - more opaque
    const panelBg = isLight ? 'rgba(255, 255, 255, 0.35)' : 'rgba(0, 0, 0, 0.35)';
    topBar.style.backgroundColor = panelBg;
    currentContainer.style.backgroundColor = panelBg;
    forecastSection.style.backgroundColor = panelBg;

    // Add CSS animation if not already added
    if (!document.getElementById('weather-bg-animation')) {
      const style = document.createElement('style');
      style.id = 'weather-bg-animation';
      style.textContent = `
        @keyframes gradientShift {
          0% { background-position: 0% 50%; }
          50% { background-position: 100% 50%; }
          100% { background-position: 0% 50%; }
        }
      `;
      document.head.appendChild(style);
    }
  }

  // Local cache helpers
  const cacheKey = () => `weather:${cfg.locationLabel}:${cfg.units}:${cfg.language}`;
  function saveCache(payload) {
    try {
      localStorage.setItem(cacheKey(), JSON.stringify({ t: Date.now(), payload }));
    } catch (e) {
      // ignore quota
    }
  }
  function loadCache() {
    try {
      const s = localStorage.getItem(cacheKey());
      if (!s) return null;
      return JSON.parse(s);
    } catch {
      return null;
    }
  }

  // Proxy support
  function inferProxyBase() {
    // If provided, use it
    if (cfg.proxy_base && typeof cfg.proxy_base === 'string') return cfg.proxy_base;
    // If globally provided by host page
    if (typeof window.PLUGIN_PROXY_BASE === 'string') return window.PLUGIN_PROXY_BASE;
    // Fallback guesses on same origin (adjust to your server if needed)
    // Common patterns:
    //  - /api/proxy?url=...
    //  - /proxy?url=...
    //  - /plugin/proxy?url=...
    return '/api/proxy?url=';
  }

  function withTimeout(promise, ms) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort('timeout'), ms);
    return Promise.race([
      promise(ctrl.signal),
      new Promise((_, rej) => {
        ctrl.signal.addEventListener('abort', () => rej(new Error('timeout')));
      })
    ]).finally(() => clearTimeout(t));
  }

  async function fetchJSONDirect(url, timeoutMs = 8000, customHeaders = {}) {
    return withTimeout(async (signal) => {
      // cachedFetch: weather endpoints (NWS / Open-Meteo) keep their last
      // successful payload so when the network goes down the panel still
      // shows the most recent forecast/observation rather than going blank.
      // Falls back to plain fetch on older cached players that haven't
      // picked up the signage_offline.js helper yet.
      const _fetch = window.cachedFetch || fetch;
      const res = await _fetch(url, { 
        signal, 
        credentials: 'omit', 
        cache: 'no-cache',
        headers: {
          'Accept': 'application/json',
          ...customHeaders
        }
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }, timeoutMs);
  }

  async function fetchJSONViaProxy(targetUrl, timeoutMs = 8000) {
    const base = inferProxyBase();
    const proxyUrl = `${base}${encodeURIComponent(targetUrl)}`;
    return withTimeout(async (signal) => {
      const _fetch = window.cachedFetch || fetch;
      const res = await _fetch(proxyUrl, { 
        signal, 
        credentials: 'omit', 
        cache: 'no-cache',
        headers: {
          'Accept': 'application/json'
        }
      });
      if (!res.ok) throw new Error(`Proxy HTTP ${res.status}`);
      return res.json();
    }, timeoutMs);
  }

  async function fetchJSON(targetUrl, customHeaders = {}) {
    // Try direct access first (NWS API supports CORS for browsers)
    try {
      return await fetchJSONDirect(targetUrl, 9000, customHeaders);
    } catch (e) {
      console.warn('Direct fetch failed:', e && e.message ? e.message : e);

      // Fall back to proxy only if configured
      if (cfg.use_proxy || cfg.proxy_base || window.PLUGIN_PROXY_BASE) {
        try {
          return await fetchJSONViaProxy(targetUrl, 10000);
        } catch (proxyError) {
          console.error('Proxy fetch also failed:', proxyError && proxyError.message ? proxyError.message : proxyError);
          throw proxyError;
        }
      }

      // No proxy configured, re-throw original error
      throw e;
    }
  }

  function parseLocation(loc) {
    if (!loc) return null;
    const s = String(loc).trim();
    const m = s.match(/^\s*(-?\d+(\.\d+)?)\s*,\s*(-?\d+(\.\d+)?)\s*$/);
    if (m) return { lat: parseFloat(m[1]), lon: parseFloat(m[3]), label: cfg.locationLabel || `${m[1]},${m[3]}` };
    // US ZIP (5 digits, optional -4). Open-Meteo's geocoder doesn't index
    // ZIPs, so detect them here and route through zippopotam below.
    if (/^\d{5}(-\d{4})?$/.test(s)) return { zip: s.slice(0, 5) };
    return { query: s };
  }

  async function geocodeZip(zip) {
    try {
      const url = `https://api.zippopotam.us/us/${zip}`;
      console.log('[Weather] Geocoding ZIP:', zip);
      const data = await fetchJSON(url);
      if (data && data.places && data.places.length) {
        const p = data.places[0];
        return {
          lat: parseFloat(p.latitude),
          lon: parseFloat(p.longitude),
          label: cfg.locationLabel
            || `${p['place name']}, ${p['state abbreviation']} ${zip}`
        };
      }
    } catch (e) {
      console.warn('[Weather] ZIP geocode failed:', e && e.message ? e.message : e);
    }
    return null;
  }

  async function geocodeIfNeeded(loc) {
    if (!loc) return null;
    if (loc.lat != null && loc.lon != null) {
      console.log('[Weather] Using direct coordinates:', loc);
      return loc;
    }
    if (loc.zip) {
      const z = await geocodeZip(loc.zip);
      if (z) {
        console.log('[Weather] ZIP geocoded to:', z);
        return z;
      }
      // Fall through to Open-Meteo as a last resort (some non-US ZIP-like
      // queries may still resolve as place names there).
      loc = { query: loc.zip };
    }
    try {
      const url = `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(loc.query)}&count=1&language=${encodeURIComponent(cfg.language)}&format=json`;
      console.log('[Weather] Geocoding query:', loc.query);
      const data = await fetchJSON(url);
      console.log('[Weather] Geocoding response:', data);
      if (data && data.results && data.results.length) {
        const r = data.results[0];

        // Build label: prefer state/region (admin1) for US locations, otherwise use country
        let locationLabel = r.name;
        if (r.admin1) {
          // Use state/region if available (e.g., "Virginia")
          // Convert to abbreviation for US states if it's a full state name
          const stateAbbrev = getStateAbbreviation(r.admin1);
          locationLabel += `, ${stateAbbrev}`;
        } else if (r.country) {
          locationLabel += `, ${r.country}`;
        }

        const result = { lat: r.latitude, lon: r.longitude, label: locationLabel };
        console.log('[Weather] Geocoded to:', result);
        return result;
      } else {
        console.warn('[Weather] No geocoding results found for:', loc.query);
      }
    } catch (e) {
      console.error('Geocoding failed', e);
    }
    return null;
  }

  // Convert full state names to abbreviations
  function getStateAbbreviation(stateName) {
    const stateMap = {
      'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR', 'California': 'CA',
      'Colorado': 'CO', 'Connecticut': 'CT', 'Delaware': 'DE', 'Florida': 'FL', 'Georgia': 'GA',
      'Hawaii': 'HI', 'Idaho': 'ID', 'Illinois': 'IL', 'Indiana': 'IN', 'Iowa': 'IA',
      'Kansas': 'KS', 'Kentucky': 'KY', 'Louisiana': 'LA', 'Maine': 'ME', 'Maryland': 'MD',
      'Massachusetts': 'MA', 'Michigan': 'MI', 'Minnesota': 'MN', 'Mississippi': 'MS', 'Missouri': 'MO',
      'Montana': 'MT', 'Nebraska': 'NE', 'Nevada': 'NV', 'New Hampshire': 'NH', 'New Jersey': 'NJ',
      'New Mexico': 'NM', 'New York': 'NY', 'North Carolina': 'NC', 'North Dakota': 'ND', 'Ohio': 'OH',
      'Oklahoma': 'OK', 'Oregon': 'OR', 'Pennsylvania': 'PA', 'Rhode Island': 'RI', 'South Carolina': 'SC',
      'South Dakota': 'SD', 'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT', 'Vermont': 'VT',
      'Virginia': 'VA', 'Washington': 'WA', 'West Virginia': 'WV', 'Wisconsin': 'WI', 'Wyoming': 'WY'
    };

    return stateMap[stateName] || stateName; // Return abbreviation if found, otherwise return original
  }

  async function fetchWeatherNWS(lat, lon) {
    // Note: Browsers cannot set User-Agent header (security restriction)
    // NWS API supports CORS and should work without it from browsers

    // Step 1: Get the grid point for this location
    const pointsUrl = `https://api.weather.gov/points/${lat.toFixed(4)},${lon.toFixed(4)}`;
    console.log('[Weather] Fetching NWS grid point:', pointsUrl);

    const pointsData = await fetchJSON(pointsUrl);
    console.log('[Weather] NWS points response:', pointsData);

    if (!pointsData || !pointsData.properties) {
      throw new Error('Failed to get NWS grid point - no properties in response');
    }

    const forecastUrl = pointsData.properties.forecast;
    const observationStationsUrl = pointsData.properties.observationStations;

    console.log('[Weather] Forecast URL:', forecastUrl);
    console.log('[Weather] Observation Stations URL:', observationStationsUrl);

    // Step 2: Get observation stations
    let currentObservation = null;
    try {
      const stationsData = await fetchJSON(observationStationsUrl);
      if (stationsData && stationsData.features && stationsData.features.length > 0) {
        // Get the first (nearest) station
        const stationId = stationsData.features[0].properties.stationIdentifier;
        const observationUrl = `https://api.weather.gov/stations/${stationId}/observations/latest`;
        console.log('[Weather] Fetching current observation from:', observationUrl);

        currentObservation = await fetchJSON(observationUrl);
        console.log('[Weather] Current observation received:', currentObservation);
      }
    } catch (err) {
      console.warn('[Weather] Current observation fetch failed:', err);
    }

    // Step 3: Get the forecast
    const forecast = await fetchJSON(forecastUrl);

    console.log('[Weather] Forecast received:', forecast);
    return { forecast, currentObservation, pointsData };
  }

  async function fetchOpenMeteoCurrentWeather(lat, lon, units) {
    const tempUnit = units === 'imperial' ? 'fahrenheit' : 'celsius';
    const windUnit = units === 'imperial' ? 'mph' : 'ms';
    const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat.toFixed(4)}&longitude=${lon.toFixed(4)}&current=temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m,is_day&temperature_unit=${tempUnit}&wind_speed_unit=${windUnit}&timezone=auto`;

    console.log('[Weather] Fetching Open-Meteo current weather:', url);
    const data = await fetchJSON(url);
    console.log('[Weather] Open-Meteo current weather received:', data);

    return data;
  }

  async function fetchOpenMeteoFull(lat, lon, units) {
    // Full Open-Meteo payload (current + 7-day daily) used when NWS is
    // unreachable or returns no usable data (e.g. coordinates outside the
    // US coverage area). Keeps the panel showing live data instead of
    // falling all the way back to the localStorage cache.
    const tempUnit = units === 'imperial' ? 'fahrenheit' : 'celsius';
    const windUnit = units === 'imperial' ? 'mph' : 'ms';
    const url = `https://api.open-meteo.com/v1/forecast`
      + `?latitude=${lat.toFixed(4)}&longitude=${lon.toFixed(4)}`
      + `&current=temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m,is_day`
      + `&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max`
      + `&temperature_unit=${tempUnit}&wind_speed_unit=${windUnit}&timezone=auto&forecast_days=7`;
    console.log('[Weather] Fetching Open-Meteo full forecast:', url);
    const data = await fetchJSON(url);
    const cur = data.current || {};
    const daily = data.daily || {};
    return {
      current_weather: {
        temperature: cur.temperature_2m,
        weathercode: cur.weather_code,
        windspeed:   cur.wind_speed_10m,
        is_day:      cur.is_day != null ? cur.is_day === 1 : null
      },
      current: {
        relative_humidity_2m: cur.relative_humidity_2m,
        precipitation:        cur.precipitation || 0,
        uv_index:             null
      },
      daily: {
        time:                          daily.time || [],
        weathercode:                   daily.weather_code || [],
        temperature_2m_max:            daily.temperature_2m_max || [],
        temperature_2m_min:            daily.temperature_2m_min || [],
        night_only:                    (daily.time || []).map(() => false),
        precipitation_probability_max: daily.precipitation_probability_max || []
      }
    };
  }

  async function fetchWeather(lat, lon, units) {
    const coordsStr = `${lat.toFixed(4)},${lon.toFixed(4)}`;
    debugEl.innerHTML = `<div>Coordinates: ${coordsStr}</div><div>Source: NWS/NOAA</div><div>Method: Direct CORS</div><div>Loading...</div>`;

    try {
      const nwsData = await fetchWeatherNWS(lat, lon);

      // Check if we need to fetch fallback current weather from Open-Meteo
      // We need the fallback if:
      // 1. No observation data at all, OR
      // 2. Observation exists but temperature value is null/undefined
      let openMeteoCurrentWeather = null;
      const hasValidObservation = nwsData.currentObservation 
        && nwsData.currentObservation.properties 
        && nwsData.currentObservation.properties.temperature?.value != null;

      if (!hasValidObservation) {
        console.warn('[Weather] No valid NWS observation data (temperature is N/A), fetching current weather from Open-Meteo fallback');
        try {
          openMeteoCurrentWeather = await fetchOpenMeteoCurrentWeather(lat, lon, units);
        } catch (meteoError) {
          console.warn('[Weather] Open-Meteo fallback also failed:', meteoError);
        }
      }

      debugEl.innerHTML = `<div>✓ SUCCESS</div><div>Location: ${coordsStr}</div><div>Source: NWS/NOAA (weather.gov)</div><div>Current: ${openMeteoCurrentWeather ? 'Open-Meteo' : 'NWS Observation'}</div><div>Grid: ${nwsData.pointsData?.properties?.gridId || 'N/A'}</div><div>Press 'D' to hide</div>`;
      return convertNWSToStandardFormat(nwsData, units, openMeteoCurrentWeather);
    } catch (nwsError) {
      console.error('[Weather] NWS API Error:', nwsError);

      // NWS only covers the US. Rather than punting to a stale local
      // cache (and showing the "Cached data" banner forever), try
      // Open-Meteo for the full forecast. This also recovers gracefully
      // from the occasional NWS 5xx outage.
      try {
        console.warn('[Weather] Falling back to Open-Meteo full forecast');
        const data = await fetchOpenMeteoFull(lat, lon, units);
        debugEl.innerHTML = `<div>✓ SUCCESS (fallback)</div><div>Location: ${coordsStr}</div><div>Source: Open-Meteo</div><div>NWS error: ${nwsError.message}</div><div>Press 'D' to hide</div>`;
        return data;
      } catch (meteoError) {
        console.error('[Weather] Open-Meteo full fallback also failed:', meteoError);
      }

      // More detailed error message
      let errorDetails = nwsError.message;
      if (errorDetails.includes('404')) {
        errorDetails = `HTTP 404 - Location may be outside US coverage area or API endpoint changed`;
      } else if (errorDetails.includes('403')) {
        errorDetails = `HTTP 403 - Access denied (possible CORS or rate limit issue)`;
      }

      debugEl.innerHTML = `<div style="color:#ff6b6b">✗ NWS FAILED</div><div>Error: ${errorDetails}</div><div>Coordinates: ${coordsStr}</div><div>Check browser console (F12) for details</div><div>Press 'D' to hide</div>`;
      throw new Error(`NWS API failed: ${nwsError.message}`);
    }
  }

  function convertNWSToStandardFormat(nwsData, units, openMeteoFallback = null) {
    const forecast = nwsData.forecast;
    if (!forecast || !forecast.properties || !forecast.properties.periods) {
      throw new Error('Invalid NWS forecast data');
    }

    const periods = forecast.properties.periods;
    const observation = nwsData.currentObservation;

    console.log('[Weather] NWS Periods received:', periods.length);
    console.log('[Weather] First 3 periods:', periods.slice(0, 3).map(p => ({
      name: p.name,
      temp: p.temperature,
      isDaytime: p.isDaytime,
      shortForecast: p.shortForecast,
      startTime: p.startTime
    })));

    // Group periods by day to get daily highs/lows
    const dailyData = {};
    periods.forEach((period, idx) => {
      // Convert to Date object and extract local date (YYYY-MM-DD format)
      const periodDate = new Date(period.startTime);
      const year = periodDate.getFullYear();
      const month = String(periodDate.getMonth() + 1).padStart(2, '0');
      const day = String(periodDate.getDate()).padStart(2, '0');
      const date = `${year}-${month}-${day}`;

      if (!dailyData[date]) {
        dailyData[date] = { highs: [], lows: [], weatherCodes: [], precipProbs: [], periods: [] };
      }
      if (period.isDaytime) {
        dailyData[date].highs.push(period.temperature);
        dailyData[date].weatherCodes.push(mapNWSToWMO(period.shortForecast));
      } else {
        dailyData[date].lows.push(period.temperature);
      }
      dailyData[date].precipProbs.push(period.probabilityOfPrecipitation?.value || 0);
      dailyData[date].periods.push(period.name);
    });

    const dates = Object.keys(dailyData).sort().slice(0, 8);

    console.log('[Weather] Daily data summary:');
    dates.forEach(date => {
      const d = dailyData[date];
      console.log(`  ${date}: Highs=[${d.highs.join(',')}] Lows=[${d.lows.join(',')}] Periods=[${d.periods.join(', ')}]`);
    });

    const daily = {
      time: dates,
      weathercode: dates.map(d => {
        const dd = dailyData[d];
        // Prefer day weather code if available, otherwise use 0 (clear)
        return dd.weatherCodes[0] || 0;
      }),
      // If a day has no daytime period (e.g. checking late evening — NWS only
      // returns "Tonight" + future days), the high is missing. Mark night_only
      // so the renderer can show a single "Tonight: X°" instead of "—° / X°".
      temperature_2m_max: dates.map(d => dailyData[d].highs.length > 0 ? Math.max(...dailyData[d].highs) : null),
      temperature_2m_min: dates.map(d => dailyData[d].lows.length > 0  ? Math.min(...dailyData[d].lows)  : null),
      night_only:        dates.map(d => dailyData[d].highs.length === 0 && dailyData[d].lows.length > 0),
      precipitation_probability_max: dates.map(d => Math.max(...dailyData[d].precipProbs, 0))
    };

    console.log('[Weather] Final daily forecast:', daily);

    // Try current observation first, then fall back to Open-Meteo if needed
    let currentTemp, currentWind, currentHumidity, currentDescription, currentWeatherCode;
    // Authoritative day/night flag from Open-Meteo (only set when we use the
    // Open-Meteo branch). Falls back to a clock-hour heuristic in renderCurrent
    // when null.
    let currentIsDay = null;

    // Check if observation has valid AND recent temperature data.
    // NWS observation stations sometimes return stale readings (hours old) or
    // are missing data. We only trust observations less than 2 hours old.
    const STALE_OBS_MS = 2 * 60 * 60 * 1000; // 2 hours
    let hasValidObservation = observation
      && observation.properties
      && observation.properties.temperature?.value != null;

    if (hasValidObservation) {
      const obsTimeStr = observation.properties.timestamp;
      if (obsTimeStr) {
        const obsAge = Date.now() - new Date(obsTimeStr).getTime();
        if (obsAge > STALE_OBS_MS) {
          console.warn(`[Weather] NWS observation is stale (${Math.round(obsAge/60000)} min old) — falling back to Open-Meteo`);
          hasValidObservation = false;
        }
      }
    }

    if (hasValidObservation) {
      const obs = observation.properties;
      console.log('[Weather] Using NWS current observation data:', obs);

      // Temperature from observation (in Celsius, convert if needed)
      const tempC = obs.temperature.value;
      currentTemp = units === 'imperial' ? (tempC * 9/5) + 32 : tempC;

      // Wind speed -- NWS observations may report in m/s OR km/h depending
      // on the station; the unit is in obs.windSpeed.unitCode. Previously we
      // always assumed m/s and multiplied by 2.237 (m/s -> mph), which made
      // a 10 km/h breeze read as 22 mph and a 19 km/h gust as 41 mph. Pick
      // the right factor based on unitCode.
      const windRaw  = obs.windSpeed?.value;
      const windUnit = obs.windSpeed?.unitCode || '';
      let windMps = null;
      if (windRaw != null) {
        if (windUnit.indexOf('km_h') >= 0) {
          windMps = windRaw / 3.6;            // km/h -> m/s
        } else if (windUnit.indexOf('m_s') >= 0) {
          windMps = windRaw;                  // already m/s
        } else {
          // Unknown unit -- assume km/h since that's NWS's documented default
          // for the v3 /observations/latest endpoint.
          windMps = windRaw / 3.6;
        }
      }
      currentWind = windMps != null
        ? (units === 'imperial' ? windMps * 2.237 : windMps)
        : null;

      // Humidity
      currentHumidity = obs.relativeHumidity?.value || null;

      // Weather description and code
      currentDescription = obs.textDescription || periods[0].shortForecast;
      currentWeatherCode = mapNWSToWMO(currentDescription);

    } else if (openMeteoFallback && openMeteoFallback.current) {
      // Fall back to Open-Meteo current weather
      console.log('[Weather] Using Open-Meteo current weather fallback:', openMeteoFallback.current);
      const meteo = openMeteoFallback.current;

      currentTemp = meteo.temperature_2m;
      currentWind = meteo.wind_speed_10m;
      currentHumidity = meteo.relative_humidity_2m;
      currentWeatherCode = meteo.weather_code || 0;
      currentDescription = WMO_CODES[currentWeatherCode] || periods[0].shortForecast;
      // Open-Meteo returns is_day=1 for daytime, 0 for night based on actual
      // sunrise/sunset for the queried lat/lon -- use this as the authoritative
      // day/night signal instead of guessing from local clock hours.
      if (meteo.is_day != null) currentIsDay = meteo.is_day === 1;

    } else {
      // No current data available from either source
      console.warn('[Weather] No current observation available - current conditions will show N/A');
      currentTemp = null;
      currentWind = null;
      currentHumidity = null;
      // Use forecast description for weather icon
      currentDescription = periods[0].shortForecast;
      currentWeatherCode = mapNWSToWMO(currentDescription);
    }

    return {
      current_weather: {
        temperature: currentTemp,
        weathercode: currentWeatherCode,
        windspeed: currentWind,
        is_day: currentIsDay
      },
      current: {
        relative_humidity_2m: currentHumidity,
        precipitation: 0,
        uv_index: null
      },
      daily: daily
    };
  }

  function mapNWSToWMO(shortForecast) {
    const desc = (shortForecast || '').toLowerCase();
    if (desc.includes('sunny') || desc.includes('clear')) return 0;
    if (desc.includes('mostly clear') || desc.includes('mostly sunny')) return 1;
    if (desc.includes('partly')) return 2;
    if (desc.includes('cloudy') || desc.includes('overcast')) return 3;
    if (desc.includes('fog')) return 45;
    if (desc.includes('drizzle')) return 51;
    if (desc.includes('rain') && desc.includes('heavy')) return 65;
    if (desc.includes('rain') && desc.includes('light')) return 61;
    if (desc.includes('rain')) return 63;
    if (desc.includes('snow') && desc.includes('heavy')) return 75;
    if (desc.includes('snow')) return 71;
    if (desc.includes('shower')) return 80;
    if (desc.includes('thunderstorm') || desc.includes('t-storm')) return 95;
    return 2; // default to partly cloudy
  }

  function updateDateTime() {
    const now = new Date();
    const options = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
    dateEl.textContent = now.toLocaleDateString(cfg.language, options);
    timeEl.textContent = now.toLocaleTimeString(cfg.language, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  // Create SVG icons for weather stats
  function getStatIcon(type) {
    const icons = {
      wind: '<svg viewBox="0 0 100 100"><path d="M10,30 Q30,20 50,30 T90,30" stroke="#87CEEB" stroke-width="4" fill="none" stroke-linecap="round"/><path d="M10,50 Q30,40 50,50 T90,50" stroke="#87CEEB" stroke-width="4" fill="none" stroke-linecap="round"/><path d="M10,70 Q30,60 50,70 T90,70" stroke="#87CEEB" stroke-width="4" fill="none" stroke-linecap="round"/></svg>',
      humidity: '<svg viewBox="0 0 100 100"><path d="M50,10 C30,35 20,50 20,65 C20,80 33,90 50,90 C67,90 80,80 80,65 C80,50 70,35 50,10 Z" fill="#4A9EFF" stroke="#357ABD" stroke-width="2"/><ellipse cx="40" cy="60" rx="8" ry="12" fill="rgba(255,255,255,0.3)"/></svg>',
      precipitation: '<svg viewBox="0 0 100 100"><ellipse cx="50" cy="25" rx="28" ry="20" fill="#4682B4"/><ellipse cx="68" cy="28" rx="22" ry="16" fill="#5A96C8"/><ellipse cx="32" cy="28" rx="18" ry="14" fill="#3070A0"/><g stroke="#4169E1" stroke-width="3" stroke-linecap="round"><line x1="35" y1="50" x2="32" y2="75"/><line x1="50" y1="50" x2="47" y2="75"/><line x1="65" y1="50" x2="62" y2="75"/></g></svg>',
      uv: '<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="18" fill="#FFD700"/><g stroke="#FFD700" stroke-width="3.5" stroke-linecap="round"><line x1="50" y1="8" x2="50" y2="20"/><line x1="50" y1="80" x2="50" y2="92"/><line x1="8" y1="50" x2="20" y2="50"/><line x1="80" y1="50" x2="92" y2="50"/><line x1="18" y1="18" x2="27" y2="27"/><line x1="73" y1="73" x2="82" y2="82"/><line x1="82" y1="18" x2="73" y2="27"/><line x1="27" y1="73" x2="18" y2="82"/></g></svg>'
    };
    return icons[type] || '';
  }

  function createStatCard(iconType, label, value) {
    const card = document.createElement('div');
    card.style.display = 'flex';
    card.style.flexDirection = 'column';
    card.style.alignItems = 'center';
    card.style.justifyContent = 'center';
    card.style.padding = '1.2vmin 1.5vmin';
    card.style.backgroundColor = 'rgba(255, 255, 255, 0.08)';
    card.style.borderRadius = '1vmin';
    card.style.minWidth = 'calc(45% - 0.75vmin)';
    card.style.flex = '1 1 calc(50% - 1.5vmin)';

    const iconEl = document.createElement('div');
    iconEl.innerHTML = getStatIcon(iconType);
    iconEl.style.width = '4.5vmin';
    iconEl.style.height = '4.5vmin';
    iconEl.style.marginBottom = '0.6vmin';

    const valueEl = document.createElement('div');
    valueEl.textContent = value;
    valueEl.style.fontSize = '2.8vmin';
    valueEl.style.fontWeight = '700';
    valueEl.style.lineHeight = '1';

    const labelEl = document.createElement('div');
    labelEl.textContent = label;
    labelEl.style.fontSize = '1.8vmin';
    labelEl.style.opacity = '0.75';
    labelEl.style.marginTop = '0.4vmin';

    card.appendChild(iconEl);
    card.appendChild(valueEl);
    card.appendChild(labelEl);
    return card;
  }

  function setLoadingUI(msg = '') {
    placeEl.textContent = msg;
    weatherIconEl.innerHTML = '';
    tempEl.textContent = '';
    descEl.textContent = '';
    statsGrid.innerHTML = '';
    footEl.textContent = '';
    forecastContainer.innerHTML = '';
  }

  function setErrorUI(msg = 'Weather unavailable') {
    placeEl.textContent = 'Set Location';
    weatherIconEl.innerHTML = '<div class="wx-icon"><svg viewBox="0 0 100 100"><g stroke="#FF6B6B" stroke-width="8" stroke-linecap="round"><line x1="25" y1="25" x2="75" y2="75"/><line x1="75" y1="25" x2="25" y2="75"/></g></svg></div>';
    tempEl.textContent = '—';
    descEl.textContent = msg;
    statsGrid.innerHTML = '';
    forecastContainer.innerHTML = '';
    // Keep last footEl if we had stale cache info
  }

  function renderForecast(dailyData, units, currentCode, currentIsDay) {
    forecastContainer.innerHTML = '';
    if (!dailyData || !dailyData.time || dailyData.time.length === 0) return;

    console.log('[Weather] Daily forecast data:', dailyData);

    const tUnit = units === 'imperial' ? '°F' : '°C';
    const precipUnit = units === 'imperial' ? 'in' : 'mm';
    const today = new Date();
    today.setHours(0, 0, 0, 0); // Reset to midnight for comparison

    let daysRendered = 0;
    for (let i = 0; i < dailyData.time.length && daysRendered < 7; i++) {
      // Parse YYYY-MM-DD as local date, not UTC (to avoid timezone offset issues)
      const [year, month, day] = dailyData.time[i].split('-').map(Number);
      const forecastDate = new Date(year, month - 1, day); // month is 0-indexed

      // Skip if this is yesterday's data
      if (forecastDate < today) {
        console.log(`[Weather] Skipping old date: ${dailyData.time[i]}`);
        continue;
      }

      const dayCard = document.createElement('div');
      dayCard.style.display = 'grid';
      dayCard.style.gridTemplateColumns = '1.5fr 1fr auto';
      dayCard.style.alignItems = 'center';
      dayCard.style.gap = '1.5vmin';
      dayCard.style.padding = '1vmin 2vmin';
      dayCard.style.backgroundColor = 'rgba(255, 255, 255, 0.05)';
      dayCard.style.borderRadius = '0.8vmin';
      dayCard.style.borderLeft = '0.4vmin solid rgba(255, 255, 255, 0.2)';

      const dayName = forecastDate.toLocaleDateString(cfg.language, { weekday: 'long' });
      const isToday = forecastDate.getTime() === today.getTime();

      const dayLabel = document.createElement('div');
      dayLabel.textContent = isToday ? 'Today' : dayName;
      dayLabel.style.fontSize = '3vmin';
      dayLabel.style.fontWeight = '600';
      dayLabel.style.textAlign = 'left';

      // Middle section - Icon and precipitation
      const iconPrecipGroup = document.createElement('div');
      iconPrecipGroup.style.display = 'flex';
      iconPrecipGroup.style.alignItems = 'center';
      iconPrecipGroup.style.gap = '1vmin';

      const icon = document.createElement('div');
      // For 'Today' card, prefer the current observation's weather code over
      // the daily summary -- the daily code is often the dominant condition
      // (e.g. 'Mostly Sunny') and doesn't match what's happening right now.
      let weatherCode = dailyData.weathercode ? dailyData.weathercode[i] : null;
      let iconIsDay = true;
      if (isToday && currentCode != null) {
        weatherCode = currentCode;
        iconIsDay = (currentIsDay != null) ? !!currentIsDay : true;
      }
      console.log(`[Weather] Day ${i} (${dayLabel.textContent}): code=${weatherCode} isDay=${iconIsDay}`);
      icon.innerHTML = getWeatherIcon(weatherCode, iconIsDay);
      icon.style.width = '6vmin';
      icon.style.height = '6vmin';
      icon.style.flexShrink = '0';

      const precipInfo = document.createElement('div');
      precipInfo.style.display = 'flex';
      precipInfo.style.alignItems = 'center';
      precipInfo.style.gap = '0.5vmin';
      precipInfo.style.fontSize = '2.2vmin';
      precipInfo.style.opacity = '0.85';

      const precipProb = dailyData.precipitation_probability_max ? dailyData.precipitation_probability_max[i] : 0;
      if (precipProb > 0) {
        precipInfo.textContent = `${precipProb}%`;
      }

      iconPrecipGroup.appendChild(icon);
      iconPrecipGroup.appendChild(precipInfo);

      // Temperature group
      const tempGroup = document.createElement('div');
      tempGroup.style.display = 'flex';
      tempGroup.style.gap = '2vmin';
      tempGroup.style.alignItems = 'center';
      tempGroup.style.justifyContent = 'flex-end';

      const tempHigh = document.createElement('div');
      const tempLow = document.createElement('div');
      const highVal = dailyData.temperature_2m_max ? dailyData.temperature_2m_max[i] : null;
      const lowVal  = dailyData.temperature_2m_min ? dailyData.temperature_2m_min[i] : null;
      const nightOnly = dailyData.night_only && dailyData.night_only[i];

      if (nightOnly && lowVal != null) {
        // Evening view — NWS no longer returns a daytime period for "today".
        // Show only the night low with a small "Tonight" label so the user
        // doesn't see a bogus 0° high.
        tempHigh.textContent = 'Tonight';
        tempHigh.style.fontSize = '2.2vmin';
        tempHigh.style.opacity = '0.65';
        tempHigh.style.fontWeight = '500';

        tempLow.textContent = `${Math.round(lowVal)}°`;
        tempLow.style.fontSize = '3.2vmin';
        tempLow.style.fontWeight = '700';
        tempLow.style.opacity = '1';
      } else {
        const high = highVal != null ? Math.round(highVal) : '—';
        const low  = lowVal  != null ? Math.round(lowVal)  : '—';

        tempHigh.textContent = `${high}°`;
        tempHigh.style.fontSize = '3.2vmin';
        tempHigh.style.fontWeight = '700';

        tempLow.textContent = `${low}°`;
        tempLow.style.fontSize = '3.2vmin';
        tempLow.style.opacity = '0.6';
      }

      tempGroup.appendChild(tempHigh);
      tempGroup.appendChild(tempLow);

      dayCard.appendChild(dayLabel);
      dayCard.appendChild(iconPrecipGroup);
      dayCard.appendChild(tempGroup);
      forecastContainer.appendChild(dayCard);
      daysRendered++;
    }
  }

  function renderUI(data, label, units, staleSinceTs = null) {
    const cw = (data && data.current_weather) || {};
    const current = (data && data.current) || {};
    const tUnit = units === 'imperial' ? '°F' : '°C';
    const wUnit = units === 'imperial' ? 'mph' : 'm/s';
    const precipUnit = units === 'imperial' ? 'in' : 'mm';

    placeEl.textContent = label || cfg.location || 'Set Location';
    // Day/night flag: prefer Open-Meteo's authoritative is_day field; fall
    // back to a clock-hour heuristic when it's not available (NWS branch).
    const isDay = (cw.is_day != null) ? !!cw.is_day : isDaytime();
    weatherIconEl.innerHTML = getWeatherIcon(cw.weathercode, isDay);
    tempEl.textContent = cw.temperature != null ? `${Math.round(cw.temperature)}${tUnit}` : '—';
    const code = (cw.weathercode != null ? cw.weathercode : null);
    descEl.textContent = (code != null && WMO_CODES.hasOwnProperty(code)) ? WMO_CODES[code] : '';

    // Set dynamic background based on current weather
    setWeatherBackground(cw.weathercode);

    // Update stats grid with current conditions
    statsGrid.innerHTML = '';

    // Wind
    if (cw.windspeed != null) {
      statsGrid.appendChild(createStatCard('wind', 'Wind', `${Math.round(cw.windspeed)} ${wUnit}`));
    }

    // Humidity
    if (current.relative_humidity_2m != null) {
      statsGrid.appendChild(createStatCard('humidity', 'Humidity', `${Math.round(current.relative_humidity_2m)}%`));
    }

    // Precipitation
    if (current.precipitation != null && current.precipitation > 0) {
      statsGrid.appendChild(createStatCard('precipitation', 'Precip', `${current.precipitation.toFixed(1)} ${precipUnit}`));
    }

    // UV Index
    if (current.uv_index != null) {
      const uvLevel = current.uv_index <= 2 ? 'Low' : current.uv_index <= 5 ? 'Moderate' : current.uv_index <= 7 ? 'High' : 'Very High';
      statsGrid.appendChild(createStatCard('uv', 'UV Index', `${Math.round(current.uv_index)} ${uvLevel}`));
    }

    // Render 7-day forecast
    if (data && data.daily) {
      renderForecast(data.daily, units, cw.weathercode, cw.is_day);
    }

    if (staleSinceTs) {
      const mins = Math.max(1, Math.round((Date.now() - staleSinceTs) / 60000));
      footEl.textContent = `⚠ Cached data (${mins} min old)`;
    } else {
      footEl.textContent = '';
    }
  }

  async function render() {
    setLoadingUI();

    // If parent player says we're offline, go straight to cache
    if (window.SIGNAGE_OFFLINE) {
      const cached = loadCache();
      if (cached && cached.payload) {
        renderUI(cached.payload, cfg.locationLabel || cfg.location || '', cfg.units, cached.t);
        return;
      }
    }

    try {
      const loc = parseLocation(cfg.location || '');
      const geo = await geocodeIfNeeded(loc);

      if (!geo) {
        // Try cache as fallback if no geocode and we have prior data
        const cached = loadCache();
        if (cached && cached.payload) {
          // Keep the previously rendered location label -- if we are
          // here because we are offline, telling the user to "set a
          // location" is misleading (one is already set, we just can't
          // geocode right now). Show whatever label the cached payload
          // had so the panel looks unchanged.
          var label = cfg.locationLabel
            || (cached.payload && cached.payload.__label)
            || cfg.location
            || '';
          renderUI(cached.payload, label, cfg.units, cached.t);
          return;
        }
        setErrorUI('Set a location in plugin settings');
        return;
      }

      const data = await fetchWeather(geo.lat, geo.lon, cfg.units);
      // Always use the configured location label
      data.__label = cfg.locationLabel || geo.label || cfg.location || '';
      saveCache(data);

      renderUI(data, data.__label, cfg.units, null);
    } catch (e) {
      console.error('Weather render error:', e && e.message ? e.message : e);

      // Fallback to cache if available
      const cached = loadCache();
      if (cached && cached.payload) {
        // Use configured label even for cached data
        renderUI(cached.payload, cfg.locationLabel || cfg.location || '', cfg.units, cached.t);
        return;
      }
      setErrorUI('Weather unavailable');
    }
  }

  // Initial render + interval
  updateDateTime();
  setInterval(updateDateTime, 1000); // Update time every second

  render();
  const secs = cfg.refresh_seconds;
  setInterval(render, secs * 1000);

  // Re-render immediately when online state changes (e.g. server came back)
  window.addEventListener('signage:online_changed', () => render());

  // Advance to next playlist item after duration, unless looping
  const shouldLoop = !!cfgRaw.loop;
  const duration   = Math.max(1, parseInt(cfgRaw.duration || 30, 10));
  if (!shouldLoop) {
    setTimeout(() => {
      try { window.parent.postMessage({ type: 'signage:complete' }, '*'); } catch {}
    }, duration * 1000);
  }

  // Tips in console for admins
  console.log('[Weather Plugin] Config:', cfg);
  if (!(cfg.location || '').trim()) {
    console.warn('[Weather Plugin] No location configured. Tip: use "lat,lon" (e.g., "40.71,-74.01") to avoid geocoding dependencies.');
  }
  if (!(cfg.use_proxy || cfg.proxy_base) && typeof window.PLUGIN_PROXY_BASE === 'undefined') {
    console.log('[Weather Plugin] If your environment blocks external requests or CSP is strict, set cfg.use_proxy=true and cfg.proxy_base="/api/proxy?url=" (or your server proxy).');
  }
})();