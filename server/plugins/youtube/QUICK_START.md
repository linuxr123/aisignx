# YouTube Plugin - Quick Start Guide

## ✅ What Was Fixed

Your YouTube plugin has been completely overhauled to work flawlessly:

1. ✅ **Fixed video stopping issues** - Videos now stop reliably
2. ✅ **Multiple video support** - Add as many videos as you want
3. ✅ **Sequential playback** - Videos play in order, one after another
4. ✅ **Random playback** - Videos play in random order
5. ✅ **Duration control** - Set how long each video plays
6. ✅ **Session mode** - Play one video per session when duration = 0
7. ✅ **Better error handling** - Automatically skips broken videos
8. ✅ **Timer cleanup** - No more memory leaks or stuck videos

## 🎮 How to Use

### Simple Setup (One Video Per Session)
```
1. Add multiple YouTube URLs (one per line) in "YouTube URLs or IDs" field
2. Set "Duration per video" to 0
3. Set "Playback mode" to "sequential"
4. Save

Result: Each time the display starts, it plays the next video in your list, 
        then stops. Perfect for daily featured videos!
```

### Continuous Playlist
```
1. Add multiple YouTube URLs (one per line)
2. Set "Duration per video" to desired seconds (e.g., 60)
3. Choose "sequential" or "random" playback mode
4. Save

Result: Videos play continuously, switching every X seconds
```

### Single Looping Video (Classic Mode)
```
1. Add ONE video URL in the "Single YouTube URL" field
2. Set "Loop current video" to YES
3. Leave "Duration per video" at 0
4. Save

Result: One video plays on repeat (old behavior)
```

## 📋 Field Reference

- **YouTube URLs or IDs**: Paste video links, one per line
- **Playback mode**: "sequential" or "random"
- **Duration per video**: 
  - `0` = Play one full video per session
  - `> 0` = Play each video for this many seconds
- **Loop current video**: Only works when NOT in playlist mode
- **Start/End times**: Clip videos to specific timestamps

## 🔍 Examples

**Example URLs you can paste (one per line):**
```
https://youtu.be/dQw4w9WgXcQ
https://www.youtube.com/watch?v=jNQXAC9IVRw
https://youtube.com/shorts/abc123xyz
oHg5SJYRHA0
```

All formats work: full URLs, short URLs, video IDs only!

## ⚙️ Files Modified

- `plugin.json` - Updated to v2.0.0 with new configuration fields
- `main.js` - Complete rewrite with all fixes and new features
- `README.md` - Full documentation

## 🚀 Ready to Use!

Your plugin is now fully functional and ready to use in your AISignX system.
Just configure it in your playlist and enjoy flawless YouTube video playback!
