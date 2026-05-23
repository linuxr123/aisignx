# Changelog

## Version 3.1.0 (Current)

### 🎉 New Feature: Dynamic Text Sizing for Vertical Mode

#### **Adaptive Vertical Scaling**
- Vertical mode now **automatically adjusts text size** based on screen height
- Calculates optimal font sizes to fit content appropriately
- Scaling factors:
  - Viewport height
  - Number of stocks configured
  - Target: 2-3 stocks visible for optimal readability
- Maintains minimum font sizes for readability
- All text elements scale proportionally (symbol, name, price, change, separators)

#### **Smart Sizing Algorithm**
```
Base calculation:
- Uses 80% of viewport height
- Targets 2-3 stocks visible at once
- Scales from baseline 400px item height
- Minimum font sizes ensure readability on any screen
```

#### **Benefits**
- Perfect for portrait displays of any size
- Automatically adapts from phone screens to large digital signage
- No manual configuration needed
- Maintains professional appearance across all screen sizes

### 📝 Documentation Updates
- Updated all guides to mention dynamic sizing
- Added technical details about scaling algorithm
- Enhanced usage tips for vertical mode

---

## Version 3.0.0

### 🎉 Major Features Added

#### **Scroll Direction Control**
- Added `direction` configuration option
- Choose between `horizontal` (default) or `vertical` scrolling
- Perfect for both landscape and portrait displays
- **Vertical mode automatically scales text size** based on screen height and number of stocks

#### **Smart Duration Control**
- Added `duration` configuration option
- **duration = 0** (default): Auto-calculates time to show all stocks completely based on scroll_speed
- **duration > 0**: Fixed duration per cycle with seamless resume after data refresh
- Animation continues from where it left off when data refreshes

#### **Dynamic Text Sizing (Vertical Mode)**
- Automatically calculates optimal font sizes for vertical displays
- Scales all text elements proportionally based on:
  - Viewport height
  - Number of stocks configured
  - Target visibility (2-3 stocks on screen for readability)
- Ensures content fits appropriately on any screen size
- Maintains readability with minimum font size limits

#### **Enhanced Configurability**
- Full control over scroll speed (pixels per second)
- Configurable refresh interval
- Theme options (dark, light, auto)
- All options exposed in plugin.json schema

### 🔧 Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `symbols` | string | `"AAPL,MSFT,GOOGL,NVDA,TSLA"` | Comma-separated stock symbols |
| `apikey` | string | `""` | Your Finnhub API key (required) |
| `direction` | string | `"horizontal"` | Scroll direction (`horizontal` or `vertical`) |
| `scroll_speed` | number | `50` | Pixels per second (20-200) |
| `duration` | number | `0` | 0=auto, >0=fixed seconds per cycle |
| `theme` | string | `"dark"` | Color theme (`dark`, `light`, or `auto`) |
| `refresh_seconds` | number | `60` | Data refresh interval (minimum 30) |

### 📝 Files Updated
- ✅ `main.js` - Core functionality with direction and duration support
- ✅ `plugin.json` - Updated schema with new configuration options
- ✅ `README.md` - Updated documentation
- ✅ `SETUP_GUIDE.md` - Comprehensive setup instructions
- ✅ `TICKER_GUIDE.md` - Visual guide for both scroll modes
- ✅ `demo.html` - Demo page with all configuration options
- ✅ `CHANGELOG.md` - This file

### 🎨 Visual Improvements
- Clean separation of horizontal and vertical layout styles
- Optimized padding and spacing for both directions
- Smooth GPU-accelerated animations
- Seamless infinite loop scrolling

### 🚀 Performance
- GPU-accelerated CSS transforms
- Efficient animation using CSS keyframes
- Smart content measurement for accurate timing
- Minimal DOM manipulation

---

## Version 2.1.0 (Previous)

### Features
- Horizontal scrolling ticker
- Basic scroll speed control
- Theme support (dark/light/auto)
- Finnhub.io API integration
- Auto-refresh capability

---

## Migration Guide: 2.1.0 → 3.0.0

### Breaking Changes
None! Version 3.0.0 is fully backward compatible.

### New Defaults
If you don't specify `direction` or `duration`, the plugin uses:
- `direction: "horizontal"` (same behavior as v2.1.0)
- `duration: 0` (auto-calculates, shows all stocks)

### Recommended Updates
1. Add `direction` to your config if you want vertical scrolling
2. Keep `duration: 0` for best experience (auto-shows all stocks)
3. Update your documentation to mention vertical mode support

### Example Config Update

**Old (v2.1.0):**
```javascript
{
  symbols: "AAPL,MSFT,GOOGL",
  apikey: "YOUR_KEY",
  scroll_speed: 50,
  theme: "dark",
  refresh_seconds: 60
}
```

**New (v3.0.0):**
```javascript
{
  symbols: "AAPL,MSFT,GOOGL",
  apikey: "YOUR_KEY",
  direction: "horizontal",    // NEW: or "vertical"
  scroll_speed: 50,
  duration: 0,                // NEW: 0=auto, or custom seconds
  theme: "dark",
  refresh_seconds: 60
}
```

---

**Enjoy the new features! 🎉**
