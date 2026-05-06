# Lyric Display

Synced Spotify lyrics, themed to your album art. A clean desktop overlay that follows whatever you're playing.

## Features

- Real-time synced lyrics (sourced from LRCLib, with lyrics.ovh fallback)
- Animated background that pulls colors from the current album art
- Drag, resize, and snap to side-of-screen for sidebar use
- Fullscreen mode (F11)
- Multi-script support: Latin, Arabic (proper shaping + RTL), Chinese / Japanese / Korean, Thai, Devanagari
- Three visual themes: Normal, Minimal, Neon
- Drop your own `.lrc` files for tracks the lyrics services don't have
- Adjustable lyric offset, lyric caching, customizable FPS and background quality

## Download

Grab the latest `LyricDisplay.exe` from the [Releases page](https://github.com/nabil2149/sptld/releases/latest). Windows only for now.

## Setup

This app reads your Spotify "Now Playing", so you'll create your own free Spotify Developer app. Takes about 2 minutes.

1. Go to https://developer.spotify.com/dashboard and log in.
2. Click **Create app**.
3. Fill in:
   - **App name**: anything you want
   - **Redirect URI**: `http://127.0.0.1:8888/callback` &nbsp;*(exact — no trailing slash)*
   - Check the **Web API** box
4. Save → open the new app → **Settings** → copy your **Client ID**.
5. Run Lyric Display once. It'll open the config folder for you.
6. Open `config.json` and paste your Client ID:
```json
   { "client_id": "paste_your_id_here" }
```
7. Save the file and reopen the app. Your browser will open Spotify's login page once — authorize it and you're done.

## Controls

| Action | How |
|---|---|
| Open / close menu | **M** |
| Toggle fullscreen | **F11** |
| Exit app | **Esc** (when menu is closed) |
| Move window | Drag the top of the window |
| Resize window | Drag any edge |
| Override lyrics for current song | Drop a `.lrc` file onto the window |
| Navigate menu | **↑ / ↓** to select, **← / →** to change value |

## Troubleshooting

- **A black command-prompt window opens alongside the app**: That's expected — closing it will close the app. Just minimize it.
- **"Invalid redirect URI"**: Your redirect URI in step 3 doesn't match exactly. Trailing slashes count, `http` vs `https` counts.
- **"User not registered"**: In your Spotify Developer dashboard go to **Settings → User Management** and add your own Spotify account email.
- **Exe won't launch / missing DLL error**: Install the [Microsoft Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe).
- **No lyrics for a song**: The lyrics services don't have it. You can drop your own `.lrc` file onto the window — it'll cache for future plays.

## Building from source

Requires Python 3.13+.

```bash
git clone https://github.com/nabil2149/sptld.git
cd sptld
pip install -r requirements.txt
python lyric_display.py
```

To compile the exe yourself, install [Nuitka](https://nuitka.net/) and run:

```bash
python -m nuitka --onefile --windows-console-mode=force --windows-icon-from-ico=logo.ico --include-package=spotipy --include-package=arabic_reshaper --include-package=bidi --include-package=PIL --include-package=pygame --include-package=numpy --include-package=requests --include-package=platformdirs --include-package=charset_normalizer --include-package=urllib3 --include-package=certifi --include-package=idna --include-package-data=certifi --include-data-dir=fonts=fonts --include-data-files=logo.png=logo.png --output-filename=LyricDisplay.exe --output-dir=dist lyric_display.py
```

## Credits

- Lyrics: [LRCLib](https://lrclib.net/) and [lyrics.ovh](https://lyrics.ovh/)
- Arabic shaping: [arabic-reshaper](https://github.com/mpcabd/python-arabic-reshaper) + [python-bidi](https://github.com/MeirKriheli/python-bidi)
- Fonts: [Amiri](https://github.com/alif-type/amiri), [Noto Sans](https://fonts.google.com/noto), Spotify Mix

## License

MIT — see [LICENSE](LICENSE) for details.
