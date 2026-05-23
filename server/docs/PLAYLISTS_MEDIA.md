# AISignX — Playlists and Media

Full reference for the media library, playlists, and content configuration.

---

## Media Types

| Type | Formats | Notes |
|---|---|---|
| Image | JPG, PNG, GIF, WebP | Static or animated (GIF). Duration = seconds on screen |
| Video | MP4, WebM, MOV | H.264 MP4 recommended for best compatibility |
| Webpage | Any URL | Rendered in a sandboxed iframe |
| Plugin | YouTube, Weather, Stocks, RSS, Clock, Radar | Dynamic live content — see [PLUGINS.md](PLUGINS.md) |

---

## Uploading Media

1. Go to **Media** in the sidebar
2. Click **Add Media**
3. Select the type and provide the content:
   - **Image/Video:** drag and drop or click to browse
   - **Webpage:** enter the URL
   - **Plugin:** select plugin type and fill in its fields
4. Set the duration and aspect mode
5. Click **Save / Upload**

### Duration

| Type | Behaviour |
|---|---|
| Image | Shows for exactly this many seconds |
| Video | The server probes the uploaded file and stores the detected length. Set `0` to play the full video. Set a value to use as a hard cap |
| Webpage | Shows for this many seconds, or advances when the page signals `signage:complete` |
| Plugin | Advances when the plugin signals completion, or after the duration if no signal |

For video media, **Use detected length** resets the media duration to the
probed video length. In the Media Library bulk bar, **Use video length** applies
that reset to all selected video rows and ignores non-video rows.

### Aspect Mode

| Mode | Behaviour |
|---|---|
| `fit` | Entire content visible — letterbox or pillarbox bars on sides |
| `fill` | Content fills the full screen, cropped if needed |
| `stretch` | Content stretched to fill — may distort aspect ratio |

Aspect mode can be set at the media level (default) and overridden per playlist item.

---

## Thumbnails

- **Images:** thumbnail generated immediately on upload
- **Videos:** thumbnail extracted from the first frame using FFmpeg
- **Webpages:** screenshot taken by Playwright/Chromium in the background
- If generation fails, a placeholder icon is shown — this does not affect playback

---

## Playlists

A playlist is an ordered list of media items that plays in a loop on a display.

### Creating a Playlist

1. Go to **Playlists** in the sidebar
2. Click **New Playlist**
3. Enter a name and optional description ? **Create**
4. Open the playlist (click its name)
5. Click **Add Item**, select media, set duration and position
6. Drag items to reorder

### Editing a Playlist Item

Click the pencil icon on any item to open the edit panel:

| Setting | Description |
|---|---|
| Duration | Seconds this item shows. Overrides the media-level default |
| Aspect Mode | How this item fills the screen |
| Clip Start | Video only — start playback at this second |
| Clip End | Video only — stop playback at this second and advance |

For video playlist items:
- `0` means play the full detected video length, or the clip range if Clip End
  is set.
- **Use detected length** resets one item to the media file's detected length.
- The playlist bulk bar has **Use video length** to reset selected video items
  at once. Non-video selections are ignored.

### Video Clip Ranges

Play only a specific portion of a video by setting Clip Start and Clip End on a playlist item.

**Example — same video three times with different clips:**

| Position | File | Clip Start | Clip End | What plays |
|---|---|---|---|---|
| 1 | promo.mp4 | 0 | 30 | First 30 seconds |
| 2 | weather plugin | — | — | Full plugin |
| 3 | promo.mp4 | 30 | 60 | Seconds 30–60 |
| 4 | promo.mp4 | 60 | 90 | Seconds 60–90 |

- Leave both fields blank to play the full video
- Set duration to `0` to let the player use the full detected video length.
- Clip times are stored per playlist item — the media file is never modified
- The same video can appear multiple times with different clips

### Reordering Items

Drag and drop items on the playlist detail page to change their order. Changes are saved immediately.

### Copying a Playlist

Open a playlist and click **Copy Playlist** to duplicate it with all its items and settings. The copy is named "Copy of [original name]".

### Deleting a Playlist

Open a playlist and click **Delete Playlist**. All playlist items are deleted automatically — you do not need to remove items first. The underlying media files are not deleted.

### Live Updates to Playing Displays

When you change a playlist that is currently playing on a display:
- The change is detected within 2 seconds by the SSE connection
- The display loads the new playlist on the next slide transition
- The currently showing slide is not interrupted

---

## Assigning a Playlist to a Display

### Default Playlist

1. Go to **Displays** and open the display
2. Under Default Playlist, select your playlist
3. Click **Save**

The default playlist plays whenever no schedule is active for this display.

### Via Schedule

For time-based playback, assign the playlist to a schedule:
1. Go to **Schedules ? Create Schedule**
2. Select the playlist and target display/group
3. Set the time/day rules

See [USER_GUIDE.md](USER_GUIDE.md) for full scheduling instructions.

---

## Aspect Modes in Detail

### fit
The entire media item is visible. Black (or background colour) bars appear on the sides or top/bottom to fill the screen.

Best for: images and videos that must not be cropped (infographics, charts, text-heavy content).

### fill
The media scales up to fill the entire screen. Parts of the content may be cropped.

Best for: full-bleed background photos and promotional videos where the subject is centred.

### stretch
The media is stretched to exactly fit the screen. This will distort non-matching aspect ratios.

Best for: content specifically designed for the display resolution.

---

## Offline Playback

The browser/Electron player uses the service worker (`sw.js`) to cache:
- Static player assets
- Media files served from `/uploads/`
- The player HTML and recent playlist payloads

Android uses its native WebView request interceptor and disk cache for the same
purpose, including range-aware video serving when offline.

If the display loses connection to the server, it continues playing from cache
where the playlist and media have already been loaded. When the server comes
back, the display reconnects and picks up the latest playlist/settings.

Synchronized groups use server-anchored wall-clock timing. If a client starts
late or drifts, it jumps to the item that should currently be playing rather
than finishing the stale item.

---

## Best Practices

- Use H.264 MP4 for videos — widest compatibility with all display types
- Keep videos under 200 MB — compress with HandBrake at 4–8 Mbps for 1080p
- For webpages, test the URL in an incognito browser tab first — it must work without login
- Use display groups and schedules rather than one playlist per display for easier management
- Always set a default playlist on every display as a fallback
- Give playlists clear names that include context: "Lobby AM", "Cafeteria Lunch Menu", "Weekend Loop"