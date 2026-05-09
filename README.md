# AutoblowSync for Stash

A [Stash](https://github.com/stashapp/stash) plugin that syncs scene playback with your **Autoblow AI Ultra** device using funscripts. Play, pause, seek and on/off toggles in the Stash video player are mirrored to the device in real time via a persistent WebSocket connection.

---

## Features

- 🔄 Real-time sync of play / pause / seek
- 🧰 Floating toolbar inside the Stash UI (drag to move, minimize, dock to bottom on mobile)
- ▶️ Play/Pause and master enable/disable buttons
- 🎛️ Funscript variant selector (if a scene has multiple `.funscript` files)
- ⏱️ Adjustable offset (ms) with quick `±` buttons to compensate for device/video latency
- 🔁 Manual re-upload button
- 📤 Uploads the scene's funscript to Autoblow on the fly
- ⚡ Token caching to avoid re-uploading the same script
- 🪵 Optional debug logging (off by default)

---

## Architecture

Unlike the original 1.0 release, AutoblowSync no longer routes events through Stash's plugin task queue. Instead it runs a small **standalone Python backend** that the browser connects to via WebSocket.

```
Browser (Stash UI)  ──WS──►  Python backend (localhost:7879)  ──HTTPS──►  Autoblow API
```

This means:

- ✅ **No more lag** when Stash is busy with Scan/Generate/Identify tasks
- ✅ Instant play/pause/seek reactions
- ✅ Backend auto-starts on the first scene load and keeps running in the background
- ✅ Singleton lock prevents multiple backend instances

---

## Requirements

- Stash (recent version with plugin support)
- Python 3 available to Stash
- An Autoblow AI Ultra device + a personal device token from [fun.autoblow.com](https://fun.autoblow.com)
- Scenes with an attached `.funscript` file
- Ports `7879` (WebSocket) and `7322` (singleton lock) free on `localhost`

---
## Installation
Add this repository as a plugin source in StashApp:

1. Go to Settings -> Plugins.
2. Click Sources -> Add Source.
4. Enter:
Name: AutoblowSync
URL:
5. install the AutoblowSync plugin from the Available tab.


## Manual Installation
1. Copy the `AutoblowSync` folder into your Stash `plugins` directory.
2. In Stash go to **Settings → Plugins** and click **Reload Plugins**.
3. Open **Settings → Plugins → AutoblowSync** and enter your **Autoblow Device token** and **Autoblow Server**.
4. Open any scene that has a funscript — the toolbar appears and sync starts automatically.

---

## Settings

| Setting | Description |
|---|---|
| **API Token** | Your personal Autoblow Device token. |
| **Server** | Autoblow API URL (just paste your favourite URL from [autoblow API documentation](https://developers.autoblow.com/reference/http-api-v1-autoblow/). |

The **video offset** is no longer set in the plugin config — use the ⏱ button on the toolbar instead. The value is persisted across sessions.

---

## Toolbar controls

| Control | Action |
|---|---|
| `≡` | Drag handle (move the toolbar) |
| `⏯` | Pause / resume script (without disabling) |
| Toggle | Master enable / disable |
| `⏱` | Open offset popup (`-2000` to `+2000` ms, ±50 ms steps) |
| `⟳` | Re-upload current funscript |
| Dropdown | Select funscript variant |
| LED + text | Current status (idle / ready / playing / paused / disabled) |
| `–` | Minimize toolbar |

On mobile the toolbar docks to the bottom of the screen automatically.

---

## How it works

The plugin has two parts:

- A **JavaScript** part injected into the Stash UI that builds the toolbar and listens to video events.
- A **Python backend** that talks to the Autoblow API (uploads funscripts, sends play/pause/seek commands).

The JS connects to the backend via WebSocket on `ws://localhost:7879`. On first scene load, the JS triggers a one-shot Stash plugin task that launches the backend, which then detaches and runs independently.

---

## Note on funscript upload

The funscript is uploaded to the Autoblow servers when a scene is selected, which can take a few seconds. The toolbar status text shows `uploading script` during this time and switches to `script ready` once the device is armed. If playback starts before the upload finishes, the backend will catch up automatically once `script_ready` is received.

---

## Troubleshooting

If something doesn't work, please collect logs **before** opening an issue.

### Enable verbose logging

**Browser side** (open DevTools with `F12` → Console):

```js
localStorage.setItem('ab-debug', '1')
```

Then reload the page. To turn it off again:

```js
localStorage.removeItem('ab-debug')
```

**Backend side**: Logs are written to `AutoblowSync/backend/backend.log` (rotated, max ~1 MB × 3 files). To enable debug-level logging, create an empty file named `.debug` in the `backend/` folder and restart the backend (kill the Python process or reload Stash plugins).

### Common issues

| Problem | Likely cause |
|---|---|
| No toolbar visible | Backend not running — check `backend.log`, or port `7879` already in use |
| Toolbar visible, LED red | WebSocket can't connect — check firewall / port `7879` |
| No reaction at all | Wrong / missing API token, or scene has no funscript (check the scene folder if there is a funscript located) |
| Status stuck on `uploading script` | Network issue or invalid Autoblow token |
| Device and video are out of sync | Adjust offset via the ⏱ button |
| Works once, then stops | Token expired — toggle the master switch off/on to re-auth |
| Multiple backends running | The singleton lock should prevent this; if it happens, kill all `python` processes related to AutoblowSync |
| Backend not starting | Try incognito window/private window and/or try disable your browser plugins, maybe one of those will block starting the websocket. |

### Restarting the backend

The backend is started automatically on scene load. To force a restart:

1. Kill the running Python process (`pkill -f autoblow` on Linux/macOS, Task Manager on Windows).
2. Reload the Stash page — the backend will respawn.

---

## Reporting bugs

Please include:

1. Stash version
2. Browser + OS
3. Browser console output (with debug logging enabled)
4. `backend.log` contents
5. A short description of what you did and what happened

---

## Privacy / Security note

Your Autoblow API token is stored in Stash's plugin configuration and only sent from the local Python backend to the official Autoblow API. It is **not** exposed to the browser. The WebSocket server only binds to local connections and does not transmit the token.

---

## License

MIT — see `LICENSE`.

---

## Disclaimer

This is an unofficial, community-made plugin. Not affiliated with or endorsed by Autoblow.
This plugin was largely created with the help of AI (vibecoded). I've tested it thoroughly on my end and it works well, but any feedback or code improvements from experienced devs are highly welcome!
