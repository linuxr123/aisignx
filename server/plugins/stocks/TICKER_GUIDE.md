# ✅ UPDATED: Modern Scrolling Stock Ticker (v3.1.0)

## What You Now Have

A **professional scrolling stock ticker** that displays stocks moving across or down the screen, with full control over direction, speed, and timing.

## Key Features

✨ **Horizontal or Vertical Scrolling** - Choose your scroll direction  
✨ **Dynamic Text Sizing** - Vertical mode auto-scales to fit screen height  
✨ **Smart Duration Control** - Auto-show all stocks or set custom timing  
✨ **Modern Design** - Clean, professional appearance  
✨ **Finnhub API** - Free, legal stock data  
✨ **Dark/Light Themes** - Auto-detect or manual  
✨ **Adjustable Speed** - Control scroll speed (pixels per second)  
✨ **Auto-refresh** - Updates every 60 seconds (configurable)  
✨ **Color-coded** - Green for gains, red for losses  
✨ **Seamless Resume** - Animation continues after data refresh  

## Visual Layout

### Horizontal Mode (Default)
```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│  AAPL Apple Inc $150.25 ▲ +2.50 (+1.69%) • MSFT Microsoft  →  │
│  ← scrolling continuously from right to left                    │
└─────────────────────────────────────────────────────────────────┘
```

### Vertical Mode
```
┌──────────────────────────┐
│  AAPL                    │
│  Apple Inc               │
│  $150.25                 │
│  ▲ +2.50 (+1.69%)        │
│  •                       │
│  MSFT                    │  ↓ scrolling
│  Microsoft               │  continuously
│  $380.50                 │  from top to
│  ▼ -1.25 (-0.33%)        │  bottom
│  •                       │
└──────────────────────────┘
```

The ticker displays:
- **Symbol** (AAPL)
- **Company Name** (Apple Inc)
- **Price** ($150.25)
- **Change** (▲ +2.50 (+1.69%))
- **Separator** (•)

## Configuration

```json
{
  "symbols": "AAPL, MSFT, GOOGL, NVDA, TSLA",
  "apikey": "YOUR_API_KEY",
  "direction": "horizontal",
  "scroll_speed": 50,
  "duration": 0,
  "theme": "dark",
  "refresh_seconds": 60
}
```

### Options Explained:

- **symbols** - Which stocks to show (comma-separated)
- **apikey** - Your free Finnhub API key
- **direction** - `horizontal` or `vertical`
  - `horizontal` = scrolls left (like TV news)
  - `vertical` = scrolls down (for portrait displays, auto-scales text to fit)
- **scroll_speed** - Pixels per second (20-200)
  - 30 = slow
  - 50 = medium (default)
  - 80 = fast
- **duration** - Seconds per cycle
  - `0` = auto-calculates to show all stocks (recommended)
  - `>0` = fixed duration, resumes after refresh
- **theme** - `dark`, `light`, or `auto`
- **refresh_seconds** - How often to update data

**Note**: In vertical mode, text size automatically adjusts based on your screen height and the number of stocks, ensuring optimal readability.

## Files Updated

✅ **plugin.json** - Config for ticker-style layout  
✅ **main.js** - Horizontal scrolling ticker code  
✅ **demo.html** - Test page  
✅ **README.md** - Documentation  
✅ **SETUP_GUIDE.md** - Setup instructions  

## Quick Start

1. **Get API Key**: https://finnhub.io/register (30 seconds, free)
2. **Edit demo.html**: Add your API key
3. **Open in Browser**: See it working!
4. **Configure**: Adjust symbols and speed to your liking

## How It Works

1. Fetches stock data from Finnhub API
2. Creates ticker items with stock info
3. Duplicates content 3x for seamless loop
4. Animates using CSS transform (smooth GPU animation)
5. Refreshes data every 60 seconds

## Customization

### Change Scroll Direction
```javascript
direction: "horizontal"  // Traditional left-scrolling ticker
direction: "vertical"    // Top-to-bottom scrolling (portrait displays)
```

### Change Stocks
Edit the `symbols` config:
```javascript
symbols: "AAPL, TSLA, AMD, NFLX, DIS, BA"
```

### Adjust Speed
Lower = slower, higher = faster:
```javascript
scroll_speed: 30  // Slow and easy to read
scroll_speed: 50  // Default
scroll_speed: 80  // Fast like TV news
```

### Set Duration
```javascript
duration: 0   // Auto - shows all stocks completely (recommended)
duration: 15  // Fixed 15 seconds per cycle
duration: 30  // Fixed 30 seconds per cycle
```

### Change Theme
```javascript
theme: "dark"  // Black background
theme: "light" // White background
theme: "auto"  // Match system preference
```

## No Interactive Elements

This is a **display-only ticker**. There are:
- ❌ No input boxes
- ❌ No add/remove buttons
- ❌ No user interaction

Just a clean, continuously scrolling ticker showing your chosen stocks.

## Perfect For

- Digital signage (horizontal or vertical)
- Office displays
- Portrait displays (use vertical mode)
- Landscape displays (use horizontal mode)
- Home automation displays
- TV dashboards
- Status boards
- Background displays

## API Info

**Finnhub.io Free Tier:**
- 60 API calls per minute
- Real-time US stock quotes
- No credit card needed
- 100% legal and free

With 5 stocks and 60-second refresh, you use ~5 calls/minute. Well within limits!

---

**Your ticker is ready to use!** 🎉

Just add your API key and watch those stocks scroll!
