# Quick Setup Guide

## 🎉 Your Stock Market Plugin Has Been Modernized!

### What Changed:

#### ✨ **New Features**
1. **Horizontal or Vertical Scrolling** - Choose your preferred scroll direction
2. **Smart Duration Control** - Auto-show all stocks or set custom timing
3. **Dynamic Text Sizing** - Vertical mode automatically adjusts text size to fit screen height
4. **Smooth Scrolling** - Continuous scroll animation with seamless loops
5. **Real-time Updates** - Fresh data every 60 seconds (configurable)
6. **Professional Design** - Clean, modern UI with smooth animations
7. **Adjustable Speed** - Control how fast the ticker scrolls (pixels per second)
8. **Dark/Light/Auto Themes** - Matches your system preferences
9. **Color-coded Changes** - Green for gains, red for losses
10. **Resume on Refresh** - Animation continues seamlessly after data updates

#### 🔧 **New API Provider**
- **Finnhub.io** - Free, legal, and reliable
- No credit card required
- 60 API calls/minute on free tier
- Real-time US stock quotes

### 🚀 How to Get Started:

#### Step 1: Get Your FREE API Key
1. Go to https://finnhub.io/register
2. Sign up with your email (free, no credit card)
3. Copy your API key

#### Step 2: Configure the Plugin

**Option A: Edit plugin.json defaults**

Open `plugin.json` and update the `"default"` values in the schema:

```json
{
  "schema": [
    {
      "name": "symbols",
      "default": "AAPL, MSFT, GOOGL, NVDA, TSLA"  ← Edit this
    },
    {
      "name": "apikey",
      "default": "YOUR_API_KEY_HERE"  ← Add your key here
    },
    {
      "name": "direction",
      "default": "horizontal"  ← or "vertical"
    },
    {
      "name": "scroll_speed",
      "default": 50  ← Pixels per second
    },
    {
      "name": "duration",
      "default": 0  ← 0=auto (show all stocks), or custom seconds
    },
    {
      "name": "theme",
      "default": "dark"  ← Change if desired
    },
    {
      "name": "refresh_seconds",
      "default": 60  ← Change interval
    }
  ]
}
```

**Option B: Configure through your plugin system**

If your system has a configuration UI, it will read the schema and let you set:
- API Key
- Stock symbols
- Scroll direction (horizontal/vertical)
- Scroll speed (pixels per second)
- Duration (0 = auto, or custom seconds)
- Theme (dark/light/auto)
- Refresh interval

**Option C: For testing with demo.html**

Edit the `window.PLUGIN_CONFIG` object in `demo.html`:

```javascript
window.PLUGIN_CONFIG = {
  symbols: "AAPL, MSFT, GOOGL, NVDA, TSLA",
  apikey: "YOUR_API_KEY_HERE",
  direction: "horizontal",  // or "vertical"
  scroll_speed: 50,          // pixels per second
  duration: 0,               // 0=auto (show all), or custom seconds
  theme: "dark",             // dark, light, or auto
  refresh_seconds: 60        // data refresh interval
};
```

#### Step 3: Test It Out
Open `demo.html` in your browser to test the plugin locally:

1. Edit `demo.html` and add your API key
2. Open in browser
3. Watch the stocks scroll!

### 📝 Configuration Options:

| Option | Type | Values | Description |
|--------|------|--------|-------------|
| `symbols` | string | Comma-separated | Stocks to display in ticker |
| `apikey` | string | Your Finnhub key | Required for data |
| `direction` | string | horizontal, vertical | Scroll direction |
| `scroll_speed` | number | 20-200 | Pixels per second scroll speed |
| `duration` | number | 0 or positive | 0=auto (show all stocks), >0=fixed seconds per cycle |
| `theme` | string | dark, light, auto | Color scheme |
| `refresh_seconds` | number | 30+ | Auto-refresh interval |

### 💡 Usage Tips:

1. **Direction**: 
   - Choose `horizontal` for traditional ticker
   - Choose `vertical` for portrait displays
   - Vertical mode automatically scales text to fit your screen height
2. **Scroll Speed**: Lower numbers = slower scroll, higher = faster (20-100 recommended)
3. **Duration**: 
   - Set to **0** (recommended) to auto-show all stocks completely
   - Set to **>0** for fixed timing (e.g., 15 seconds per cycle)
4. **More Stocks**: Add more symbols for a longer ticker
5. **Themes**: Use "auto" to match system dark/light mode
6. **Performance**: Ticker uses GPU-accelerated CSS animations for smooth 60fps
7. **Vertical Optimization**: Text automatically resizes based on screen height and number of stocks

### 🎨 What Makes It Modern:

- **Smooth Scrolling**: Infinite loop ticker with seamless animation
- **Adaptive Sizing**: Vertical mode dynamically adjusts text to fit screen height
- **Professional Typography**: System font stack (-apple-system)
- **Color-coded Changes**: Green for gains, red for losses
- **GPU Accelerated**: Uses CSS transforms for smooth 60fps scrolling
- **Clean Spacing**: Proper padding between ticker items
- **Responsive Text**: Large, readable fonts that scale appropriately

### 🔒 Legal & Free:

✅ Finnhub.io is completely legal and free to use  
✅ No credit card required for free tier  
✅ 60 calls/minute is plenty for personal use  
✅ Real-time data from official sources  

### 📁 Files Updated:

- ✅ `plugin.json` - Updated configuration schema
- ✅ `main.js` - Complete rewrite with modern code
- ✅ `README.md` - Documentation
- ✅ `demo.html` - Test file for local development
- ✅ `SETUP_GUIDE.md` - This file!

### 🎯 Next Steps:

1. Get your API key from finnhub.io
2. Add it to your config
3. Customize your stock list
4. Choose scroll direction (horizontal or vertical)
5. Set duration (0 for auto, or custom timing)
6. Choose your preferred theme (dark/light/auto)
7. Adjust scroll speed to your preference
8. Enjoy your modern stock ticker!

---

**Need Help?**  
- Finnhub API Docs: https://finnhub.io/docs/api
- Free API Key: https://finnhub.io/register

**Enjoy your new modern stock portfolio! 📈**
