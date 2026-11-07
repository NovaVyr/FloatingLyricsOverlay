# Floating Lyrics Overlay

A lightweight, aesthetics-focused floating synced-lyrics overlay for the Spotify desktop app on Windows. It renders synchronized lyric lines as cards scattered across your screen with smooth animations, dynamic color theming, and festive effects.

## Features

- **Floating Lyric Cards**: Lyrics displayed as cards at randomized positions with subtle tilts and gentle upward drifting.
- **Visual Effects**: Falling snow particles, floating musical notes, and twinkling stars that bring the screen to life.
- **Dynamic Color Theming**: Card, text, and particle colors automatically adapt to the album cover of the playing track.
- **Click-Through Window**: The transparent overlay does not intercept mouse clicks, allowing seamless multitasking and gaming.
- **Auto-Synced Lyrics**: Automatically retrieves synced lyrics for tracks playing on Spotify desktop via [LRCLIB](https://lrclib.net).
- **Manual Timing Offset**: Fine-tune lyric synchronization on the fly with keyboard hotkeys (saved per song automatically).

## Requirements

- Windows 10 or 11
- Spotify desktop app (not the web player)
- Python 3.10 or newer
- Active internet connection (for fetching lyrics from LRCLIB)

## Installation

1. Create and activate a virtual environment (optional but recommended):
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

Run the script from the project root:
```powershell
python floating_lyrics.py
```

### Hotkeys
- `Ctrl+Shift+Q`: Quit the overlay.
- `Ctrl+Shift+Left`: Shift lyric timing backward by 0.25s.
- `Ctrl+Shift+Right`: Shift lyric timing forward by 0.25s.

## Configuration

Key settings can be adjusted near the top of `floating_lyrics.py`:

| Setting | Default | Description |
| :--- | :--- | :--- |
| `THEME` | `"christmas"` | Visual theme: `"christmas"`, `"winter"`, or `"midnight"` |
| `CARD_W` | `320` | Base width of lyric cards in pixels |
| `SNOW_COUNT` | `110` | Total number of animated snow particles |
| `LYRIC_OFFSET` | `0.0` | Global timing offset in seconds (+ appears earlier) |

## Notes

- Lyrics are fetched from LRCLIB and cached locally in a `lyrics_cache/` directory (git-ignored).
- Per-song timing offsets are also saved into the local cache.
- Spotify desktop app must be running; playback data is read through Windows Media Control.


## Acknowledgements
   - Developed with the help of Claude (Anthropic) and Antigravity (Google).


## Disclaimer

Not affiliated with Spotify. Lyrics belong to their respective owners.

## License

MIT License. See the [LICENSE](LICENSE) file.
