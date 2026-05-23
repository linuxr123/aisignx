# Modern Stock Ticker Plugin (v3.1.0)

## Overview
A modern, professional stock ticker plugin with horizontal or vertical scrolling, clean design, and smooth animations. Perfect for digital signage, displays, and dashboards.

## Features
- **Horizontal or Vertical Scrolling**: Choose the direction stocks scroll
- **Dynamic Text Sizing**: Vertical mode automatically adjusts to fit screen height
- **Smart Duration Control**: Set to 0 to auto-show all stocks, or override with custom duration
- **Configurable Scroll Speed**: Control animation speed in pixels per second
- **Seamless Resume**: When using custom duration, animation resumes where it left off after refresh
- **Modern UI**: Clean, professional appearance
- **Real-time Data**: Uses Finnhub.io free API (legal and no credit card required)
- **Dark/Light Themes**: Auto-detect or manual selection
- **Smooth Animations**: GPU-accelerated CSS animations

## Setup
1. Get a FREE API key at https://finnhub.io/register
2. Enter the API key in the plugin configuration
3. Add your favorite stock symbols (e.g., AAPL, MSFT, GOOGL)
4. Choose scroll direction (horizontal or vertical)
5. Adjust scroll speed (pixels per second)
6. Set duration (0 = auto-show all stocks, or custom seconds)

## Configuration
- **symbols** - Comma-separated stock symbols
- **apikey** - Your Finnhub API key (required)
- **direction** - `horizontal` or `vertical` (default: horizontal)
- **scroll_speed** - Pixels per second - controls how fast stocks scroll (default: 50)
- **duration** - Seconds per cycle:
  - **0** (default) = Auto-calculates to show all stocks completely
  - **>0** = Fixed duration, resumes where it left off after data refresh
- **theme** - `dark`, `light`, or `auto`
- **refresh_seconds** - Auto-refresh interval in seconds (default: 60, minimum: 30)

## API Source
**Finnhub.io** - Free tier includes:
- 60 API calls/minute
- Real-time US stock quotes
- No credit card required
- Completely legal and free
