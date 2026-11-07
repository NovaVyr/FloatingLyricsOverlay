"""
Floating Lyrics Overlay

- Reads currently playing track from Spotify desktop (via Windows Media Control)
- Fetches synchronized lyrics from LRCLIB (cached in lyrics_cache folder)
- Displays each lyric line as a floating card at randomized positions
- Exit: Ctrl+Shift+Q | Shift lyric timing: Ctrl+Shift+Left / Ctrl+Shift+Right

Install: pip install -r requirements.txt
"""
import asyncio
import colorsys
import ctypes
import hashlib
import io
import json
import math
import os
import queue
import random
import re
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timezone

try:
    import winrt.windows.media.control as media_control  # type: ignore
    WINRT_OK = True
except ImportError:
    media_control = None
    WINRT_OK = False

try:    # optional: extract palette colors from Spotify album artwork
    from winrt.windows.storage.streams import DataReader  # type: ignore  # noqa
except ImportError:
    DataReader = None
try:
    from PIL import Image
except ImportError:
    Image = None

# =====================================================================
# SETTINGS
# =====================================================================
THEME = "christmas"          # christmas | winter | midnight
CLICK_THROUGH = True         # mouse clicks pass through overlay
CARD_W = 320
CARD_MIN_H = 170
CARD_SCALE_RANGE = (0.85, 1.15)     # randomized card size scale
CARD_TILT_RANGE = (-4.5, 4.5)       # randomized card tilt angle (degrees), previously +-8
TILT_MIN_GAP = 2.0                  # minimum tilt difference (degrees) between new card and recent cards
TILT_ENABLED = True                 # False = cards are upright (only positions are randomized)
DYNAMIC_COLORS = True               # card/note/star colors adapt to the playing track album artwork
DRIFT_SPEED = 10                    # px/sec, slow upward card drift
SWAY = (6, 14)                      # horizontal sway range (px)
CARD_LIFETIME = (5.0, 9.0)          # card lifetime bounds in seconds
CARD_LIFETIME_FACTOR = 2.2          # lifetime = line duration x factor (clamped to bounds above)
CARD_FADE_SECONDS = 0.5
TYPE_CPS = 26                       # typewriter effect speed (chars per second)
GRID_COLS_MAX = 4                   # screen grid columns, each card gets a different cell
GRID_ROWS = 3
SNOW_COUNT = 110
NOTE_POOL = 16
NOTE_SPAWN_GAP = (0.35, 0.9)
SPARKLE_POOL = 18
STAR_POOL = 14                      # star glyphs: ✦ ✧ ✴
STAR_SPAWN_GAP = (0.4, 1.1)
LYRIC_OFFSET = 0.0                  # +seconds = lyrics appear earlier
OFFSET_STEP = 0.25
OFFSET_TOAST_SECONDS = 2.0
SPOTIFY_POLL_SECONDS = 0.5
SPOTIFY_MAX_DRIFT = 1.0             # position difference (seconds) before triggering seek
LYRIC_HTTP_TIMEOUT = 6.0
DEBUG = True                        # print debug info in terminal for troubleshooting

THEMES = {
    "christmas": dict(
        card_bg="#B01212", card_edge="#FF7A7A", card_shadow="#2B0000", text="#FFFFFF",
        accent="#FFD166", font=("Georgia", 22, "bold"),
        notes=["#FF4D4D", "#FF8A8A", "#E02020", "#FFB3B3"],
        snow="#FFFFFF", sparkle="#FFE9A8", snow_on=True,
        stars=["#FFE9A8", "#FFFFFF", "#FFD166", "#9BE7A5", "#FF8A8A"]),
    "winter": dict(
        card_bg="#DCE6F3", card_edge="#FFFFFF", card_shadow="#14233A", text="#1B2A41",
        accent="#5E9BEA", font=("Segoe UI", 21, "bold"),
        notes=["#7FB4FF", "#A8D0FF", "#5E9BEA", "#CFE5FF"],
        snow="#FFFFFF", sparkle="#BFE3FF", snow_on=True,
        stars=["#BFE3FF", "#FFFFFF", "#A8D0FF", "#FFE9A8"]),
    "midnight": dict(
        card_bg="#1D1636", card_edge="#8B6CFF", card_shadow="#05030D", text="#F3EEFF",
        accent="#B79CFF", font=("Segoe UI", 21, "bold"),
        notes=["#B79CFF", "#8B6CFF", "#D6C7FF", "#6F4BFF"],
        snow="#D6C7FF", sparkle="#FFFFFF", snow_on=False,
        stars=["#FFFFFF", "#D6C7FF", "#FFE9A8", "#B79CFF"]),
}
NOTE_CHARS = ["♪", "♫", "♬", "♩"]
NOTE_SIZES = [16, 22, 28, 36]
STAR_CHARS = ["✦", "✧", "✴"]
STAR_SIZES = [14, 18, 24, 30]
CHRISTMAS_RE = re.compile(r"christmas|xmas|natal|navidad|noel|santa|jingle|winter wonder", re.I)
KEY = "#010101"   # transparent chroma key color, do not use elsewhere
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lyrics_cache")
USER_AGENT = "FloatingLyricsOverlay/3.0"
OFFSET_KEY = "offset"   # cache key for offset


def log(*args):
    if DEBUG:
        print("[overlay]", *args, flush=True)


# =====================================================================
# Common utilities
# =====================================================================
def enable_dpi_awareness():
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def make_click_through(root):
    if sys.platform != "win32":
        return
    try:
        root.update_idletasks()
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        style = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, style | 0x80000 | 0x20 | 0x80)
    except Exception:
        pass


def key_down(vk):
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


def exit_combo_pressed():
    return sys.platform == "win32" and key_down(0x11) and key_down(0x10) and key_down(0x51)


def ease_out(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def rotate_points(points, angle, cx, cy):
    if not angle:
        return list(points)
    rad = math.radians(angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    out = []
    for i in range(0, len(points), 2):
        x, y = points[i] - cx, points[i + 1] - cy
        out.extend((cx + x * cos_a - y * sin_a, cy + x * sin_a + y * cos_a))
    return out


def rounded_points(x1, y1, x2, y2, r):
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


# =====================================================================
# Dynamic colors (derived from album artwork)
# =====================================================================
def hls_hex(h, l, s):
    r, g, b = colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return "#%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255))


def dominant_color(image_bytes):
    """Dominant artwork color (RGB) - prioritizes chromatic pixels over black/white/gray."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize((32, 32))
    sx = sy = weight = sat_sum = 0.0
    px = list(getattr(img, "get_flattened_data", img.getdata)())
    for r, g, b in px:
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        w = s * max(0.05, 1 - abs(l - 0.5) * 1.8)
        sx += math.cos(h * math.tau) * w
        sy += math.sin(h * math.tau) * w
        weight += w
        sat_sum += s
    avg_s = sat_sum / len(px)
    if weight < 0.5:                      # artwork is nearly monochrome / grayscale
        return (128, 128, 128)
    hue = (math.atan2(sy, sx) / math.tau) % 1.0
    r, g, b = colorsys.hls_to_rgb(hue, 0.5, max(0.3, min(1.0, avg_s * 2)))
    return (round(r * 255), round(g * 255), round(b * 255))


def make_palette(rgb, seed_text):
    """Generate colors for cards, notes, and stars. Without artwork -> distinct color based on track title."""
    if rgb is None:
        digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
        h, s = digest[0] / 255, 0.55 + digest[1] / 255 * 0.25
    else:
        h, l, s = colorsys.rgb_to_hls(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
        s = s if s < 0.12 else max(0.45, min(0.85, s))
    return dict(
        card_bg=hls_hex(h, 0.30, s), card_edge=hls_hex(h, 0.72, min(1.0, s + 0.1)),
        card_shadow=hls_hex(h, 0.06, s * 0.8), text="#FFFFFF",
        accent=hls_hex(h + 0.5, 0.72, 0.8), sparkle=hls_hex(h, 0.85, 0.9),
        notes=[hls_hex(h, 0.68, 0.85), hls_hex(h + 0.08, 0.72, 0.8), hls_hex(h - 0.08, 0.72, 0.8),
               hls_hex(h + 0.5, 0.70, 0.8), hls_hex(random.random(), 0.70, 0.85),
               hls_hex(random.random(), 0.70, 0.85)],
        stars=["#FFD86B", "#FFFFFF", hls_hex(h, 0.85, 0.9), hls_hex(h + 0.5, 0.80, 0.9),
               hls_hex(h + 0.15, 0.78, 0.9), hls_hex(random.random(), 0.80, 0.9)])


# =====================================================================
# Lyrics: cache, LRC parser, LRCLIB
# =====================================================================
def cache_path(artist, title, create=True):
    if create:
        os.makedirs(CACHE_DIR, exist_ok=True)
    raw = json.dumps([artist.casefold().strip(), title.casefold().strip()], ensure_ascii=False)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{artist}_{title}").strip("._-")[:80] or "track"
    return os.path.join(CACHE_DIR, f"{slug}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]}.json")


def read_cache(artist, title):
    try:
        with open(cache_path(artist, title, create=False), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_cache_fields(artist, title, **fields):
    try:
        data = read_cache(artist, title)
        data.update(fields)
        data["artist"], data["title"] = artist, title
        with open(cache_path(artist, title), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except (OSError, ValueError):
        pass


def parse_lrc(text):
    lines = []
    pattern = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
    for line in (text or "").splitlines():
        matches = list(pattern.finditer(line))
        if not matches:
            continue
        body = line[matches[-1].end():].strip()
        if not body:
            continue
        for m in matches:
            minutes, seconds, frac = m.groups()
            fs = int(frac) / (10 ** len(frac)) if frac else 0.0
            lines.append((int(minutes) * 60 + int(seconds) + fs, body))
    return sorted(lines, key=lambda row: row[0])


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=LYRIC_HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def clean_title(title):
    t = re.sub(r"\s*[\(\[].*?[\)\]]", "", title)
    t = re.sub(r"\s+[-–]\s+(remaster|live|mono|stereo|version|single|radio|\d{4}).*$", "", t, flags=re.I)
    return t.strip() or title


def fetch_lyrics(title, artist, album, duration):
    """Return list of [(seconds, text)]. Raises ConnectionError on network/LRCLIB failure."""
    cached = read_cache(artist, title).get("parsed")
    if cached:
        return [(float(t), str(s)) for t, s in cached]

    base = "https://lrclib.net/api/"
    text = None
    net_errors = 0
    params = {"track_name": title, "artist_name": artist}
    if album:
        params["album_name"] = album
    if duration > 0:
        params["duration"] = round(duration)
    try:
        text = http_json(base + "get?" + urllib.parse.urlencode(params)).get("syncedLyrics")
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            net_errors += 1
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        net_errors += 1

    if not text:
        for query in (f"{artist} {title}", f"{artist} {clean_title(title)}"):
            try:
                results = http_json(base + "search?" + urllib.parse.urlencode({"q": query}))
                found = [r for r in results if r.get("syncedLyrics")]
                if duration > 0:
                    found.sort(key=lambda r: abs(float(r.get("duration") or 0) - duration))
                if found:
                    text = found[0]["syncedLyrics"]
                    break
            except urllib.error.HTTPError as e:
                if e.code >= 500:
                    net_errors += 1
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError):
                net_errors += 1

    parsed = parse_lrc(text)
    if not parsed and net_errors >= 2:
        raise ConnectionError("Unable to reach LRCLIB (check your internet connection)")
    if parsed:   # empty results are not cached so they can be retried later
        write_cache_fields(artist, title, album=album, duration=duration, parsed=parsed)
    return parsed


# =====================================================================
# Spotify (Windows Media Control) - runs in its own thread
# =====================================================================
def to_seconds(value):
    """winrt 2.x returns datetime.timedelta; older versions return object with .duration."""
    if value is None:
        return 0.0
    if hasattr(value, "total_seconds"):
        return value.total_seconds()
    ticks = getattr(value, "duration", None)
    return ticks / 10_000_000 if ticks is not None else 0.0


class SpotifyDetector(threading.Thread):
    def __init__(self, out_queue):
        super().__init__(daemon=True)
        self.out = out_queue
        self.running = True
        self.last_err = None
        self._art_key, self._art_color, self._art_tries = None, None, 0

    def stop(self):
        self.running = False

    async def _request_manager(self):
        return await media_control.GlobalSystemMediaTransportControlsSessionManager.request_async()

    def run(self):
        if not WINRT_OK:
            self.out.put(("ERROR", "winrt package is not installed. Run: pip install -r requirements.txt"))
            return
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        manager = None
        try:
            while self.running:
                try:
                    if manager is None:
                        manager = loop.run_until_complete(self._request_manager())
                    data = loop.run_until_complete(self._read(manager))
                    self.out.put(("DATA", data))
                    self.last_err = None
                except Exception as e:   # noqa: BLE001
                    manager = None
                    msg = f"{type(e).__name__}: {e}"
                    if msg != self.last_err:
                        log("Failed to read Spotify ->", msg)
                        self.last_err = msg
                    self.out.put(("IDLE", None))
                time.sleep(SPOTIFY_POLL_SECONDS)
        finally:
            loop.close()

    async def _read_art_color(self, props):
        if DataReader is None or Image is None:
            return None
        try:
            thumb = props.thumbnail
            if thumb is None:
                return None
            stream = await thumb.open_read_async()
            size = int(stream.size)
            if size <= 0:
                return None
            reader = DataReader(stream.get_input_stream_at(0))
            await reader.load_async(size)
            buf = bytearray(size)
            reader.read_bytes(buf)
            return dominant_color(bytes(buf))
        except Exception as e:   # noqa: BLE001
            log("Failed to read album artwork ->", f"{type(e).__name__}: {e}")
            return None

    async def _read(self, manager):
        session = None
        try:
            cur = manager.get_current_session()
            if cur and "spotify" in (cur.source_app_user_model_id or "").casefold():
                session = cur
        except Exception:
            pass
        if session is None:
            for cand in manager.get_sessions():
                if "spotify" in (cand.source_app_user_model_id or "").casefold():
                    session = cand
                    break
        if session is None:
            return None

        props = await session.try_get_media_properties_async()
        title = (props.title or "").strip()
        if not title:
            return None
        artist = (props.artist or "").strip()
        album = (props.album_title or "").strip()

        key = f"{artist}\0{title}"
        if key != self._art_key:
            self._art_key, self._art_color, self._art_tries = key, None, 0
        if self._art_color is None and self._art_tries < 6:
            self._art_tries += 1
            self._art_color = await self._read_art_color(props)

        timeline = session.get_timeline_properties()
        position = to_seconds(timeline.position)
        duration = to_seconds(timeline.end_time)

        status = "stopped"
        info = session.get_playback_info()
        if info is not None and info.playback_status is not None:
            value = int(info.playback_status)       # 4 = Playing, 5 = Paused
            status = "playing" if value == 4 else "paused" if value == 5 else "stopped"

        # Spotify does not always update position every second; extrapolate from last update time.
        if status == "playing":
            updated = getattr(timeline, "last_updated_time", None)
            if isinstance(updated, datetime):
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                extra = (datetime.now(timezone.utc) - updated).total_seconds()
                if 0 <= extra <= 600 and (duration <= 0 or position + extra <= duration + 1):
                    position += extra

        return {"title": title, "artist": artist, "album": album, "duration": duration,
                "position": max(0.0, position), "status": status, "timestamp": time.perf_counter(),
                "color": self._art_color}


class LyricsWorker(threading.Thread):
    def __init__(self, track, out_queue):
        super().__init__(daemon=True)
        self.track, self.out = track, out_queue

    def run(self):
        t = self.track
        try:
            parsed = fetch_lyrics(t["title"], t["artist"], t["album"], t["duration"])
            self.out.put((t["id"], parsed, None))
        except ConnectionError as e:
            self.out.put((t["id"], [], str(e)))
        except Exception as e:   # noqa: BLE001
            self.out.put((t["id"], [], f"Failed to fetch lyrics: {type(e).__name__}"))


# =====================================================================
# Particles
# =====================================================================
class Snow:
    def __init__(self, app):
        self.app = app
        self.glyph = random.random() < 0.15
        if self.glyph:
            self.item = app.c.create_text(-50, -50, text=random.choice(["❄", "❅"]),
                                          font=("Segoe UI Symbol", random.randint(10, 18)), fill=app.th["snow"])
        else:
            self.item = app.c.create_oval(-50, -50, -45, -45, fill=app.th["snow"], outline="")
        self.respawn(initial=True)

    def respawn(self, initial=False):
        W, H = self.app.W, self.app.H
        self.bx = random.uniform(-40, W + 40)
        self.y = random.uniform(-H, H) if initial else random.uniform(-60, -10)
        self.r = random.choice([1.2, 1.6, 2.0, 2.6, 3.2, 4.0])
        self.vy = 25 + self.r * 14 + random.uniform(0, 20)
        self.amp = random.uniform(6, 30)
        self.freq = random.uniform(0.6, 1.8)
        self.phase = random.uniform(0, math.tau)

    def update(self, dt, t, wind):
        self.y += self.vy * dt
        x = self.bx + wind * (self.y / self.app.H) + math.sin(t * self.freq + self.phase) * self.amp
        if self.glyph:
            self.app.c.coords(self.item, x, self.y)
        else:
            self.app.c.coords(self.item, x - self.r, self.y - self.r, x + self.r, self.y + self.r)
        if self.y > self.app.H + 20:
            self.respawn()


class Note:
    def __init__(self, app):
        self.app = app
        self.item = app.c.create_text(-100, -100, text="♪", fill="#FFFFFF", font=("Segoe UI Symbol", 20, "bold"))
        self.active = False

    def spawn(self):
        a = self.app
        a.c.itemconfig(self.item, text=random.choice(NOTE_CHARS),
                       font=("Segoe UI Symbol", random.choice(NOTE_SIZES), "bold"),
                       fill=random.choice(a.th["notes"]))
        self.bx, self.y = random.uniform(30, a.W - 30), a.H + 40
        self.speed, self.amp = random.uniform(40, 100), random.uniform(12, 48)
        self.freq, self.phase, self.age = random.uniform(0.8, 2.4), random.uniform(0, math.tau), 0.0
        self.active = True

    def update(self, dt):
        if not self.active:
            return
        self.age += dt
        self.y -= self.speed * dt
        x = self.bx + math.sin(self.age * self.freq + self.phase) * self.amp
        self.app.c.coords(self.item, x, self.y)
        if self.y < -60:
            self.active = False
            self.app.c.coords(self.item, -100, -100)


class Sparkle:
    def __init__(self, app):
        self.app = app
        self.item = app.c.create_polygon([-50] * 16, fill=app.th["sparkle"], outline="")
        self.active = False

    def spawn(self):
        self.x, self.y = random.uniform(20, self.app.W - 20), random.uniform(20, self.app.H - 20)
        self.size, self.duration, self.age = random.uniform(6, 15), random.uniform(0.8, 1.7), 0.0
        self.app.c.itemconfig(self.item, fill=random.choice(self.app.th["stars"]))
        self.active = True

    def update(self, dt):
        if not self.active:
            return
        self.age += dt
        f = self.age / self.duration
        if f >= 1:
            self.active = False
            self.app.c.coords(self.item, *([-50] * 16))
            return
        s = self.size * math.sin(math.pi * f)
        pts = []
        for i in range(8):
            ang = i * math.pi / 4 - math.pi / 2
            r = s if i % 2 == 0 else s * 0.28
            pts += [self.x + r * math.cos(ang), self.y + r * math.sin(ang)]
        self.app.c.coords(self.item, *pts)


class StarGlyph:
    """Star symbols ✦ ✧ ✴ that fade in, twinkle, and fade out."""
    def __init__(self, app):
        self.app = app
        self.item = app.c.create_text(-100, -100, text="✦", fill="#FFFFFF", font=("Segoe UI Symbol", 16))
        self.active = False
        self.visible = True

    def spawn(self):
        a = self.app
        a.c.itemconfig(self.item, text=random.choice(STAR_CHARS), state="normal",
                       font=("Segoe UI Symbol", random.choice(STAR_SIZES)),
                       fill=random.choice(a.th["stars"]))
        self.x, self.y = random.uniform(30, a.W - 30), random.uniform(30, a.H * 0.9)
        self.duration, self.age = random.uniform(2.0, 4.0), 0.0
        self.freq, self.phase = random.uniform(4, 9), random.uniform(0, math.tau)
        self.visible = True
        self.active = True

    def update(self, dt):
        if not self.active:
            return
        self.age += dt
        f = self.age / self.duration
        if f >= 1:
            self.active = False
            self.app.c.coords(self.item, -100, -100)
            return
        self.y -= 6 * dt
        fade = min(1.0, f * 6, (1 - f) * 6)
        show = fade > 0.3 and math.sin(self.age * self.freq + self.phase) > -0.55
        if show != self.visible:
            self.visible = show
            self.app.c.itemconfig(self.item, state="normal" if show else "hidden")
        self.app.c.coords(self.item, self.x, self.y)


# =====================================================================
# Lyric Card
# =====================================================================
class Card:
    def __init__(self, app, text, cell, line_duration, tilt, scale):
        self.app, self.text, self.tilt, self.scale = app, text, tilt, scale
        self.cell_index = cell[0]
        self.lifetime = max(CARD_LIFETIME[0], min(CARD_LIFETIME[1], line_duration * CARD_LIFETIME_FACTOR))
        self.tag = f"card{id(self)}"
        c, th = app.c, app.th
        family, size, weight = th["font"]
        self.font = (family, max(12, round(size * scale)), weight)
        self.w = round(CARD_W * scale)

        tmp = c.create_text(0, 0, text=text, font=self.font, width=self.w - round(52 * scale))
        bb = c.bbox(tmp)
        c.delete(tmp)
        text_h = (bb[3] - bb[1]) if bb else 40
        self.h = max(round(CARD_MIN_H * scale), text_h + round(100 * scale))
        self.cx, self.cy = self.w / 2, self.h / 2

        def poly(x1, y1, x2, y2, r, fill):
            pts = rotate_points(rounded_points(x1, y1, x2, y2, r), tilt, self.cx, self.cy)
            c.create_polygon(pts, smooth=True, fill=fill, outline="", tags=self.tag)

        s = scale
        poly(7 * s, 12 * s, self.w + 7 * s, self.h + 12 * s, 28 * s, th["card_shadow"])
        poly(0, 0, self.w, self.h, 28 * s, th["card_edge"])
        poly(3 * s, 3 * s, self.w - 3 * s, self.h - 3 * s, 25 * s, th["card_bg"])
        poly(self.cx - 32 * s, 11 * s, self.cx + 32 * s, 19 * s, 4 * s, th["card_shadow"])

        bar_y = self.h - 30 * s
        icon = rotate_points([20 * s, bar_y - 6 * s, 32 * s, bar_y - 6 * s,
                              32 * s, bar_y + 6 * s, 20 * s, bar_y + 6 * s], tilt, self.cx, self.cy)
        c.create_polygon(icon, fill=th["accent"], outline="", tags=self.tag)
        lw = max(2, round(3 * s))
        self.bar_start, self.bar_end, self.bar_y = 46 * s, self.w - 24 * s, bar_y
        track = rotate_points([self.bar_start, bar_y, self.bar_end, bar_y], tilt, self.cx, self.cy)
        c.create_line(*track, fill=th["card_shadow"], width=lw, capstyle="round", tags=self.tag)
        start = rotate_points([self.bar_start, bar_y, self.bar_start, bar_y], tilt, self.cx, self.cy)
        self.bar = c.create_line(*start, fill=th["accent"], width=lw, capstyle="round", tags=self.tag)

        pos = rotate_points([self.cx, self.cy - 8 * s], tilt, self.cx, self.cy)
        kw = dict(text="", font=self.font, fill=th["text"], width=self.w - round(52 * s),
                  justify="center", tags=self.tag)
        try:
            self.txt = c.create_text(*pos, angle=-tilt, **kw)
        except tk.TclError:
            self.txt = c.create_text(*pos, **kw)

        # randomized position inside allocated cell
        _, x0, y0, cw, ch = cell
        self.base_x = x0 + random.uniform(0, max(0.0, cw - self.w))
        self.base_y = y0 + random.uniform(0, max(0.0, ch - self.h))
        self.cur_x, self.cur_y = self.base_x, self.base_y
        self.vy = -random.uniform(DRIFT_SPEED * 0.6, DRIFT_SPEED * 1.4)
        self.sway_amp = random.uniform(*SWAY)
        self.sway_freq = random.uniform(0.7, 1.5)
        self.phase = random.uniform(0, math.tau)
        self.age = 0.0
        self.shown = -1
        self.dying = False
        self.death_age = 0.0
        self.cur_scale = 1.0
        c.move(self.tag, self.cur_x, self.cur_y)

    def update(self, dt):
        c = self.app.c
        self.age += dt
        a = self.age
        slide = (1 - ease_out(a / 0.45)) * 50 * self.scale
        nx = self.base_x + math.sin(a * self.sway_freq + self.phase) * self.sway_amp
        ny = self.base_y + self.vy * a + slide
        c.move(self.tag, nx - self.cur_x, ny - self.cur_y)
        self.cur_x, self.cur_y = nx, ny

        if not self.dying:
            n = min(len(self.text), int(a * TYPE_CPS))
            if n != self.shown:
                self.shown = n
                c.itemconfig(self.txt, text=self.text[:n])
            f = min(1.0, a / self.lifetime)
            end_x = self.bar_start + (self.bar_end - self.bar_start) * f
            p = rotate_points([self.bar_start, self.bar_y, end_x, self.bar_y], self.tilt, self.cx, self.cy)
            c.coords(self.bar, nx + p[0], ny + p[1], nx + p[2], ny + p[3])
            if a >= self.lifetime:
                self.dying = True
                c.itemconfig(self.txt, state="hidden")
                c.itemconfig(self.bar, state="hidden")
        else:
            self.death_age += dt
            target = max(0.02, 1 - self.death_age / CARD_FADE_SECONDS)
            ratio = target / self.cur_scale
            c.scale(self.tag, nx + self.cx, ny + self.cy, ratio, ratio)
            self.cur_scale = target

    def force_die(self):
        if self.dying:
            return
        self.dying = True
        self.app.c.itemconfig(self.txt, state="hidden")
        self.app.c.itemconfig(self.bar, state="hidden")

    def is_dead(self):
        return self.dying and self.death_age >= CARD_FADE_SECONDS

    def destroy(self):
        self.app.c.delete(self.tag)


# =====================================================================
# Application
# =====================================================================
class App:
    def __init__(self, root):
        self.root = root
        self.base_th = THEMES[THEME]
        self.th = dict(self.base_th)
        self.recent_tilts = deque(maxlen=3)
        self.W, self.H = root.winfo_screenwidth(), root.winfo_screenheight()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.geometry(f"{self.W}x{self.H}+0+0")
        root.config(bg=KEY)
        root.attributes("-transparentcolor", KEY)
        self.c = tk.Canvas(root, width=self.W, height=self.H, bg=KEY, highlightthickness=0, bd=0)
        self.c.pack()
        if CLICK_THROUGH:
            make_click_through(root)
        else:
            root.bind("<Escape>", lambda e: self.shutdown())

        self.snow = [Snow(self) for _ in range(SNOW_COUNT)] if self.th["snow_on"] else []
        self.sparkles = [Sparkle(self) for _ in range(SPARKLE_POOL)]
        self.notes = [Note(self) for _ in range(NOTE_POOL)]
        self.stars = [StarGlyph(self) for _ in range(STAR_POOL)]
        self.build_grid()

        self.spotify_queue, self.lyrics_queue = queue.Queue(), queue.Queue()
        self.cards, self.lyrics, self.idx = [], [], 0
        self.current_track_id = None
        self.track_title = self.track_artist = ""
        self.playback_status = "stopped"
        self.last_sync_pos, self.last_sync_perf = 0.0, time.perf_counter()
        self.lyric_offset = LYRIC_OFFSET
        self.prev_left = self.prev_right = False
        self.art_color = None
        self.track_duration = 0.0
        self.toast_expiry = 0.0

        self.detector = SpotifyDetector(self.spotify_queue)
        self.detector.start()
        self.t0 = self.last = time.perf_counter()
        self.next_note = self.next_sparkle = self.next_star = 0.0
        root.protocol("WM_DELETE_WINDOW", self.shutdown)
        log("Overlay running. Play a song on Spotify desktop. Exit: Ctrl+Shift+Q")
        self.tick()

    # ---------- card layout grid ----------
    def build_grid(self):
        left, right = self.W * 0.05, self.W * 0.95
        top, bottom = self.H * 0.05, self.H * 0.86       # reserve space for taskbar
        cols = max(2, min(GRID_COLS_MAX, int((right - left) // (CARD_W * 1.1))))
        cw, ch = (right - left) / cols, (bottom - top) / GRID_ROWS
        self.cells = []
        for r in range(GRID_ROWS):
            for col in range(cols):
                self.cells.append((len(self.cells), left + col * cw, top + r * ch, cw, ch))
        self.recent_cells = deque(maxlen=max(1, min(5, len(self.cells) // 2)))

    def pick_cell(self):
        busy = {card.cell_index for card in self.cards if not card.dying}
        free = [cell for cell in self.cells if cell[0] not in busy and cell[0] not in self.recent_cells]
        if not free:
            free = [cell for cell in self.cells if cell[0] not in busy] or self.cells
        cell = random.choice(free)
        self.recent_cells.append(cell[0])
        return cell

    def pick_tilt(self):
        if not TILT_ENABLED:
            return 0.0
        tilt = random.uniform(*CARD_TILT_RANGE)
        for _ in range(30):
            tilt = random.uniform(*CARD_TILT_RANGE)
            if all(abs(tilt - t) >= TILT_MIN_GAP for t in self.recent_tilts):
                break
        self.recent_tilts.append(tilt)
        return tilt

    def apply_palette(self, color):
        """Adjust palette according to track. Christmas-themed songs keep the original red theme."""
        self.art_color = color
        th = dict(self.base_th)
        if DYNAMIC_COLORS and self.track_title and not CHRISTMAS_RE.search(self.track_title):
            th.update(make_palette(color, f"{self.track_artist}|{self.track_title}"))
        self.th = th

    def spawn_card(self, text, line_duration):
        card = Card(self, text, self.pick_cell(), line_duration,
                    self.pick_tilt(), random.uniform(*CARD_SCALE_RANGE))
        self.cards.append(card)

    def clear_cards(self):
        for card in self.cards:
            card.destroy()
        self.cards.clear()

    def message_card(self, text, seconds=8.0):
        self.spawn_card(text, seconds / CARD_LIFETIME_FACTOR)

    # ---------- track playback state ----------
    def playback_position(self):
        if self.playback_status == "playing":
            return max(0.0, self.last_sync_pos + time.perf_counter() - self.last_sync_perf)
        return max(0.0, self.last_sync_pos)

    def seek_index(self):
        pos = self.playback_position() + self.lyric_offset
        self.idx = 0
        while self.idx < len(self.lyrics) and self.lyrics[self.idx][0] <= pos:
            self.idx += 1
        self.clear_cards()
        if self.playback_status == "playing" and self.idx > 0:
            nxt = self.lyrics[self.idx][0] if self.idx < len(self.lyrics) else pos + 5
            ts, text = self.lyrics[self.idx - 1]
            self.spawn_card(text, max(2.0, nxt - ts))

    def on_track(self, data):
        self.current_track_id = data["id"]
        self.track_title, self.track_artist = data["title"], data["artist"]
        self.playback_status = data["status"]
        self.last_sync_pos, self.last_sync_perf = data["position"], data["timestamp"]
        self.lyrics, self.idx = [], 0
        self.lyric_offset = float(read_cache(self.track_artist, self.track_title).get(OFFSET_KEY, LYRIC_OFFSET))
        self.track_duration = float(data.get("duration") or 0.0)
        self.apply_palette(data.get("color"))
        self.clear_cards()
        log(f"Track: {self.track_artist} - {self.track_title} ({data['status']}) - searching lyrics...")
        LyricsWorker(data, self.lyrics_queue).start()

    def handle_media(self, data):
        if data is None:
            if self.playback_status == "playing":
                self.playback_status = "paused"
            return
        track_id = f"{data['artist']}\0{data['title']}"
        if track_id != self.current_track_id:
            self.on_track(dict(data, id=track_id))
            return
        if data.get("color") and data["color"] != self.art_color:
            self.apply_palette(data["color"])
        if data.get("duration"):
            self.track_duration = float(data["duration"])
        old = self.playback_status
        drift = abs(self.playback_position() - data["position"])
        self.playback_status = data["status"]
        if old != self.playback_status or drift > SPOTIFY_MAX_DRIFT:
            self.last_sync_pos, self.last_sync_perf = data["position"], data["timestamp"]
            self.seek_index()

    def process_queues(self):
        while True:
            try:
                kind, payload = self.spotify_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "DATA":
                self.handle_media(payload)
            elif kind == "IDLE":
                self.handle_media(None)
            elif kind == "ERROR":
                log(payload)
                self.message_card("winrt package not installed\n(check terminal)", 10.0)
        while True:
            try:
                track_id, lines, error = self.lyrics_queue.get_nowait()
            except queue.Empty:
                break
            if track_id != self.current_track_id:
                continue
            self.lyrics = lines
            self.seek_index()
            if error:
                log(error)
                self.message_card(f"{self.track_title}\n(failed to load lyrics)")
            elif not lines:
                log("Synced lyrics not found on LRCLIB")
                self.message_card(f"{self.track_title}\n(no lyrics found)")
            else:
                log(f"Lyrics found: {len(lines)} lines")

    # ---------- timing offset ----------
    def check_hotkeys(self):
        if sys.platform != "win32":
            return
        ctrl_shift = key_down(0x11) and key_down(0x10)
        left, right = ctrl_shift and key_down(0x25), ctrl_shift and key_down(0x27)
        delta = 0.0
        if left and not self.prev_left:
            delta = -OFFSET_STEP
        if right and not self.prev_right:
            delta = OFFSET_STEP
        self.prev_left, self.prev_right = left, right
        if delta:
            self.lyric_offset += delta
            if self.track_title:
                write_cache_fields(self.track_artist, self.track_title, **{OFFSET_KEY: self.lyric_offset})
            self.show_toast(f"Lyric offset: {self.lyric_offset:+.2f}s")
            self.seek_index()

    def show_toast(self, text):
        self.c.delete("toast")
        x, y = self.W // 2, self.H - 50
        self.c.create_polygon(rounded_points(x - 115, y - 19, x + 115, y + 19, 12), smooth=True,
                              fill="#101015", outline="#404050", tags="toast")
        self.c.create_text(x, y, text=text, font=("Segoe UI", 12, "bold"), fill="#00FFCC", tags="toast")
        self.toast_expiry = time.perf_counter() + OFFSET_TOAST_SECONDS

    # ---------- main loop ----------
    def shutdown(self):
        self.detector.stop()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def tick(self):
        now = time.perf_counter()
        dt = min(now - self.last, 0.05)
        self.last = now
        elapsed = now - self.t0
        if exit_combo_pressed():
            self.shutdown()
            return
        self.check_hotkeys()
        self.process_queues()

        playing = self.playback_status == "playing"
        if self.lyrics and playing:
            pos = self.playback_position() + self.lyric_offset
            while self.idx < len(self.lyrics) and self.lyrics[self.idx][0] <= pos:
                ts, text = self.lyrics[self.idx]
                nxt = self.lyrics[self.idx + 1][0] if self.idx + 1 < len(self.lyrics) else ts + 5
                self.spawn_card(text, nxt - ts)
                self.idx += 1

        if self.track_duration > 0 and self.playback_position() >= self.track_duration - 0.3:
            for card in self.cards:      # track ended: dismiss active cards
                card.force_die()
        for card in self.cards:
            card.update(dt)
        for card in [k for k in self.cards if k.is_dead()]:
            card.destroy()
            self.cards.remove(card)

        if self.toast_expiry and now >= self.toast_expiry:
            self.c.delete("toast")
            self.toast_expiry = 0.0

        wind = math.sin(elapsed * 0.3) * 60
        for s in self.snow:
            s.update(dt, elapsed, wind)
        if playing:     # notes and sparkles only appear when playing
            if now >= self.next_note:
                for n in self.notes:
                    if not n.active:
                        n.spawn()
                        break
                self.next_note = now + random.uniform(*NOTE_SPAWN_GAP)
            if now >= self.next_sparkle:
                for sp in self.sparkles:
                    if not sp.active:
                        sp.spawn()
                        break
                self.next_sparkle = now + random.uniform(0.15, 0.5)
            if now >= self.next_star:
                for st in self.stars:
                    if not st.active:
                        st.spawn()
                        break
                self.next_star = now + random.uniform(*STAR_SPAWN_GAP)
        for n in self.notes:
            n.update(dt)
        for st in self.stars:
            st.update(dt)
        for sp in self.sparkles:
            sp.update(dt)
        self.root.after(8, self.tick)


if __name__ == "__main__":
    enable_dpi_awareness()
    tk_root = tk.Tk()
    App(tk_root)
    tk_root.mainloop()
