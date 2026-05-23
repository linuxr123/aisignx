# YouTube Plugin v2.0.0 - Upgrade Notes

## What's New

This version completely fixes the YouTube plugin for your AISignX display system with the following improvements:

### 🎉 New Features

1. **Multiple Video Support**
   - Add multiple YouTube videos (one per line) in the new "YouTube URLs or IDs" textarea field
   - Backwards compatible with single video field

2. **Playlist Modes**
   - **Sequential**: Videos play in order, then loop back to the first
   - **Random**: Videos are selected randomly from your list

3. **Duration Control**
   - Set `video_duration` to specify how long each video plays (in seconds)
   - Set to `0` to play ONE video per session, then stop
   - When duration is `0`, each time the plugin starts, it plays the next video in the sequence

4. **Improved Video Stopping**
   - Fixed the issue where videos wouldn't stop properly
   - All timers are now properly cleared when videos end or errors occur
   - Added proper cleanup on page unload

5. **Better Error Handling**
   - If a video has an error and you have multiple videos, it automatically tries the next one
   - Clear error messages for different YouTube API errors

### 📋 Configuration Schema Changes

**New Fields:**
- `videos` (textarea) - Add multiple YouTube URLs/IDs, one per line
- `playback_mode` (select) - Choose "sequential" or "random"
- `video_duration` (integer) - Duration per video in seconds (0 = play one and stop)

**Existing Fields:** (all preserved)
- `video` - Single video (still works for backwards compatibility)
- `mute`, `loop`, `start`, `end`, `cc`, `controls`, `rel`, `branding`, `privacy_mode`, `quality`, `playback_rate`

### 🔧 How It Works

#### Scenario 1: Single Video Session Mode (`video_duration = 0`)
```
Duration: 0 seconds
Videos: [Video1, Video2, Video3]
Mode: Sequential

Session 1: Plays Video1, then stops
Session 2: Plays Video2, then stops
Session 3: Plays Video3, then stops
Session 4: Plays Video1, then stops (loops back)
```

#### Scenario 2: Continuous Playlist Mode (`video_duration > 0`)
```
Duration: 30 seconds
Videos: [Video1, Video2, Video3]
Mode: Sequential

Plays Video1 for 30s → Video2 for 30s → Video3 for 30s → Video1 for 30s → (continues...)
```

#### Scenario 3: Random Playlist Mode
```
Duration: 45 seconds
Videos: [Video1, Video2, Video3, Video4]
Mode: Random

Plays random video for 45s → another random video for 45s → continues randomly...
```

### 🐛 Bugs Fixed

1. **Video Not Stopping**: Fixed timer management - all timers are now properly cleared
2. **Memory Leaks**: Added proper cleanup on page unload
3. **End Time Issues**: Fixed monitoring of video end times
4. **State Management**: Better handling of player state changes
5. **Error Recovery**: Plugin now tries next video if current one fails (when multiple videos are available)

### 🎯 Usage Examples

**Example 1: Rotating Corporate Videos**
```
videos:
  https://youtu.be/VIDEO_ID_1
  https://youtu.be/VIDEO_ID_2
  https://youtu.be/VIDEO_ID_3

playback_mode: sequential
video_duration: 60
loop: false
```
Each video plays for 60 seconds, then moves to the next one.

**Example 2: Daily Featured Video**
```
videos:
  dQw4w9WgXcQ
  jNQXAC9IVRw
  oHg5SJYRHA0

playback_mode: sequential
video_duration: 0
```
Each time the sign starts, it plays the next video in the list for its full duration, then stops.

**Example 3: Random Background Videos**
```
videos:
  https://youtube.com/watch?v=VIDEO1
  https://youtube.com/watch?v=VIDEO2  
  https://youtube.com/watch?v=VIDEO3
  https://youtube.com/watch?v=VIDEO4

playback_mode: random
video_duration: 120
loop: false
```
Randomly selects videos and plays each for 2 minutes.

### 🔄 Migration from v1.x

Your existing single video configurations will continue to work! The plugin checks for the new `videos` field first, then falls back to the old `video` field if `videos` is empty.

No changes needed for existing setups, but you can now add more videos if you want!

### 📝 Technical Notes

- Uses `sessionStorage` to track video index in sequential mode
- Properly clears all timers (quality enforcement, time monitoring, duration timer)
- Gracefully handles `sessionStorage` not being available
- Improved quality enforcement timing
- Better error messages with specific YouTube error codes

---

**Version:** 2.0.0  
**Date:** 2026-03-04  
**Compatibility:** AISignX Display System
