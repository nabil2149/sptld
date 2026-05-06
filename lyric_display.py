from __future__ import annotations
import pygame
import sys
import time
import threading
import requests
import spotipy
from spotipy.oauth2 import SpotifyPKCE      # was SpotifyOAuth — now PKCE
from PIL import Image, ImageDraw, ImageFilter
import io
import re
import math
import os
import json
import numpy as np
import colorsys
import platformdirs
import webbrowser                            # ← NEW
import subprocess                            # ← NEW
import gc
import ctypes
import platform
import logging

# =========================
# Config dir + paths
# =========================
CONFIG_DIR = platformdirs.user_config_dir("LyricDisplay", "LyricDisplay")
os.makedirs(CONFIG_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
PREFS_FILE  = os.path.join(CONFIG_DIR, "prefs.json")
CACHE_FILE  = os.path.join(CONFIG_DIR, "lyric_cache.json")
LRC_DIR     = os.path.join(CONFIG_DIR, "lrc")
os.makedirs(LRC_DIR, exist_ok=True)

def _resource_path(rel: str) -> str:
    """Find a resource bundled next to the script/exe.
    Works in dev .py, Nuitka standalone, and Nuitka onefile."""
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, rel)

LOG_FILE = os.path.join(CONFIG_DIR, "lyric_display.log")
_handlers = [logging.FileHandler(LOG_FILE, encoding='utf-8')]
if sys.stderr is not None:
    _handlers.append(logging.StreamHandler())

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=_handlers,
)
log = logging.getLogger('lyric_display')
# =========================
# Boot + window management
# =========================
pygame.init()

# ─────────────────────────────────────────
# Native Windows drag/resize for NOFRAME window
# ─────────────────────────────────────────
_IS_WIN = platform.system() == 'Windows'

# Win32 message constants
_WM_NCLBUTTONDOWN = 0xA1
_HT_CAPTION       = 2     # drag (also enables double-click maximize + edge snap)
_HT_LEFT          = 10
_HT_RIGHT         = 11
_HT_TOP           = 12
_HT_TOPLEFT       = 13
_HT_TOPRIGHT      = 14
_HT_BOTTOM        = 15
_HT_BOTTOMLEFT    = 16
_HT_BOTTOMRIGHT   = 17

_RESIZE_BORDER     = 6    # px from window edge that count as resize zones
_DRAG_BAND_HEIGHT  = 32   # top band height that acts as a drag handle

# Window style constants for forcing native resize on a NOFRAME window
_GWL_STYLE        = -16
_WS_THICKFRAME    = 0x00040000
_WS_MAXIMIZEBOX   = 0x00010000
_WS_MINIMIZEBOX   = 0x00020000
_SWP_NOMOVE       = 0x0002
_SWP_NOSIZE       = 0x0001
_SWP_NOZORDER     = 0x0004
_SWP_FRAMECHANGED = 0x0020

_WS_CAPTION = 0x00C00000
_WS_SYSMENU = 0x00080000

def _strip_caption():
    """
    Remove the title bar but keep native resize border, snap, and minimize/maximize.
    Window starts as a normal resizable window — we just hide the caption strip on top.
    """
    if not _IS_WIN:
        return
    try:
        hwnd = pygame.display.get_wm_info()['window']
        style = ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_STYLE)
        style &= ~(_WS_CAPTION | _WS_SYSMENU)
        ctypes.windll.user32.SetWindowLongW(hwnd, _GWL_STYLE, style)
        ctypes.windll.user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_FRAMECHANGED
        )
    except Exception as e:
        log.error(f"Strip caption error: {e}")

def _hit_test(mx, my, w, h):
    """Decide which window-frame zone this mouse position should pretend to be."""
    rb = _RESIZE_BORDER
    on_top, on_bot = my < rb, my >= h - rb
    on_lft, on_rgt = mx < rb, mx >= w - rb
    if on_top and on_lft: return _HT_TOPLEFT
    if on_top and on_rgt: return _HT_TOPRIGHT
    if on_bot and on_lft: return _HT_BOTTOMLEFT
    if on_bot and on_rgt: return _HT_BOTTOMRIGHT
    if on_top: return _HT_TOP
    if on_bot: return _HT_BOTTOM
    if on_lft: return _HT_LEFT
    if on_rgt: return _HT_RIGHT
    if my < _DRAG_BAND_HEIGHT: return _HT_CAPTION
    return None

def _native_drag_resize(hit_code):
    """Hand the drag/resize off to Windows native handling."""
    if not _IS_WIN or hit_code is None or is_fullscreen:
        return
    try:
        hwnd = pygame.display.get_wm_info()['window']
        ctypes.windll.user32.ReleaseCapture()
        ctypes.windll.user32.SendMessageW(hwnd, _WM_NCLBUTTONDOWN, hit_code, 0)
    except Exception as e:
        log.error(f"Native drag/resize error: {e}")

# Cursor mapping for the resize zones
_RESIZE_CURSORS = {
    _HT_LEFT:        pygame.SYSTEM_CURSOR_SIZEWE,
    _HT_RIGHT:       pygame.SYSTEM_CURSOR_SIZEWE,
    _HT_TOP:         pygame.SYSTEM_CURSOR_SIZENS,
    _HT_BOTTOM:      pygame.SYSTEM_CURSOR_SIZENS,
    _HT_TOPLEFT:     pygame.SYSTEM_CURSOR_SIZENWSE,
    _HT_BOTTOMRIGHT: pygame.SYSTEM_CURSOR_SIZENWSE,
    _HT_TOPRIGHT:    pygame.SYSTEM_CURSOR_SIZENESW,
    _HT_BOTTOMLEFT:  pygame.SYSTEM_CURSOR_SIZENESW,
}
_last_cursor_zone = "init"   # sentinel so first call always sets cursor

def _update_cursor(W, H):
    """Show the right cursor when hovering near an edge/corner."""
    global _last_cursor_zone
    if not _IS_WIN or is_fullscreen:
        return
    zone = _hit_test(*pygame.mouse.get_pos(), W, H)
    if zone in _RESIZE_CURSORS:
        if zone != _last_cursor_zone:
            pygame.mouse.set_cursor(_RESIZE_CURSORS[zone])
            _last_cursor_zone = zone
    else:
        if _last_cursor_zone != "arrow":
            pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_ARROW)
            _last_cursor_zone = "arrow"

pygame.event.set_allowed([pygame.QUIT, pygame.KEYDOWN, pygame.VIDEORESIZE, pygame.DROPFILE, pygame.MOUSEBUTTONDOWN])
try:
    icon_img = pygame.image.load("logo.png")
    pygame.display.set_icon(icon_img)
except Exception:
    pass

gc.set_threshold(50000, 10, 10)

if platform.system() == 'Windows':
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass

DEFAULT_WINDOW_SIZE = (1280, 720)
screen = pygame.display.set_mode(DEFAULT_WINDOW_SIZE, pygame.RESIZABLE)
pygame.display.set_caption("Lyric Display")
_strip_caption()
clock = pygame.time.Clock()
FPS = 60

is_fullscreen = False
windowed_size = list(DEFAULT_WINDOW_SIZE)
_last_toggle_ts = 0.0

def set_window_mode(fullscreen: bool):
    global screen, is_fullscreen, windowed_size
    if fullscreen == is_fullscreen:
        return
    if fullscreen:
        windowed_size = list(screen.get_size())
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        is_fullscreen = True
    else:
        screen = pygame.display.set_mode(tuple(windowed_size), pygame.RESIZABLE)
        _strip_caption()
        is_fullscreen = False

# =========================
# JSON helpers (must come BEFORE anything that calls them)
# =========================
def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log.error(f"Load {path} error: {e}")
    return default

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"Save {path} error: {e}")

# =========================
# First-run setup screen
# =========================
def show_first_run_setup():
    if not os.path.exists(CONFIG_FILE):
        save_json(CONFIG_FILE, {"client_id": ""})

    webbrowser.open("https://github.com/nabil2149/sptld#setup")

    sysname = platform.system()
    try:
        if sysname == "Windows":   os.startfile(CONFIG_DIR)
        elif sysname == "Darwin":  subprocess.Popen(["open", CONFIG_DIR])
        else:                       subprocess.Popen(["xdg-open", CONFIG_DIR])
    except Exception:
        pass

    pygame.display.set_caption("Lyric Display — Setup")
    surf = pygame.display.set_mode((720, 360))
    font_big = pygame.font.SysFont("Arial", 28, bold=True)
    font_sm  = pygame.font.SysFont("Arial", 18)
    lines = [
        "Setup needed.",
        "",
        "Full guide:",
        "https://github.com/nabil2149/sptld#setup",
        "",
        "Quick version:",
        "1. Create a Spotify app at developer.spotify.com",
        "2. Paste your client_id into config.json (just opened)",
        "3. Save and reopen this app",
        "",
        "Press any key to close.",
    ]
    waiting = True
    while waiting:
        for e in pygame.event.get():
            if e.type in (pygame.QUIT, pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                waiting = False
        surf.fill((20, 20, 22))
        y = 60
        for i, ln in enumerate(lines):
            f = font_big if i == 0 else font_sm
            r = f.render(ln, True, (235, 235, 235))
            surf.blit(r, (40, y))
            y += 36 if i == 0 else 24
        pygame.display.flip()
        clock.tick(30)

# =========================
# Spotify set-up (uses helpers above)
# =========================
config = load_json(CONFIG_FILE, {"client_id": ""})
SPOTIFY_CLIENT_ID = (config.get("client_id") or "").strip()

if not SPOTIFY_CLIENT_ID:
    show_first_run_setup()
    sys.exit(0)

sp = spotipy.Spotify(auth_manager=SpotifyPKCE(
    client_id=SPOTIFY_CLIENT_ID,
    redirect_uri="http://127.0.0.1:8888/callback",
    scope="user-read-currently-playing",
    open_browser=True,
    cache_path=os.path.join(CONFIG_DIR, ".spotify_cache"),
))

# =========================
# Prefs + cache loading (also uses load_json)
# =========================
prefs = load_json(PREFS_FILE, {
    "lyric_offset_ms": 0,
    "use_album_colors": True,
    "user_color": [255, 230, 120],
    "theme": "Normal",
    "use_cache": True,
    "fps": 60,
    "bg_quality": "Performance",
})
lyric_cache_disk = load_json(CACHE_FILE, {})

def persist_prefs():
    save_json(PREFS_FILE, prefs)

def persist_cache():
    save_json(CACHE_FILE, lyric_cache_disk)

# ================
# Global state
# ================
current_lyrics: list[tuple[float, str]] = []
current_song_id: str | None = None
sync_progress_ms: int = 0
sync_timestamp: float = 0.0
is_playing: bool = False
did_resize: bool = False
auth_error: str | None = None
# FIX #2: separate surfaces for no-lyrics mode vs normal mode
album_art_surface: pygame.Surface | None = None   # raw rounded art (300px base)

current_artist: str = ""
current_song_name: str = ""
dominant_colors: list[tuple] = [(40, 40, 40), (200, 200, 200)]
track_duration_ms: int = 0

is_loading: bool = False
loading_started_at: float = 0.0

# Persistent lyric surface — reused every frame, never reallocated
_lyric_surf_cache: dict = {"surf": None, "size": (0, 0)}

# Cached lyric layout — recomputed only on song/size/font change
layout_cache: dict = {
    "song_id": None,
    "width": 0,
    "lyric_px": 0,
    "LINE_H": 0,
    "SPACING": 0,
    "wrapped_lines": [],
    "block_heights": [],
    "h_with_spacing": [],
}

# ================
# Arabic / RTL support — auto-install if needed, pure-Python fallback
# ================
_RTL_RE = re.compile(r'[\u0600-\u06FF\u0590-\u05FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]')

def _try_install_arabic_libs():
    """Pip-install Arabic libs. Skipped inside frozen/Nuitka exe builds."""
    if getattr(sys, 'frozen', False) or "__compiled__" in globals():
        return
    import subprocess
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "arabic-reshaper", "python-bidi", "-q"],
            timeout=30, capture_output=True
        )
    except Exception:
        pass

# Try importing; if missing, attempt install once, then retry
try:
    import arabic_reshaper
    from bidi.algorithm import get_display as bidi_display
    HAS_BIDI = True
    log.info("arabic-reshaper + python-bidi loaded")
except ImportError:
    log.info("Installing arabic-reshaper + python-bidi…")
    _try_install_arabic_libs()
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display as bidi_display
        HAS_BIDI = True
        log.info("arabic-reshaper installed and loaded")
    except ImportError:
        HAS_BIDI = False
        log.warning("Arabic reshaper unavailable — using built-in fallback")

# ── Pure-Python Arabic reshaper fallback ─────────────────────────────────────
# Covers the most common Arabic letter forms so text connects correctly
# even without the arabic-reshaper package.
_ARABIC_FORMS: dict[int, tuple] = {
    # (isolated, initial, medial, final)
    0x0627: ('ا', None,  None,  'ا'),  # Alef
    0x0628: ('ب', 'بـ', 'ـبـ', 'ـب'),  # Ba
    0x062A: ('ت', 'تـ', 'ـتـ', 'ـت'),  # Ta
    0x062B: ('ث', 'ثـ', 'ـثـ', 'ـث'),  # Tha
    0x062C: ('ج', 'جـ', 'ـجـ', 'ـج'),  # Jim
    0x062D: ('ح', 'حـ', 'ـحـ', 'ـح'),  # Ha
    0x062E: ('خ', 'خـ', 'ـخـ', 'ـخ'),  # Kha
    0x062F: ('د', None,  None,  'ـد'),  # Dal
    0x0630: ('ذ', None,  None,  'ـذ'),  # Dhal
    0x0631: ('ر', None,  None,  'ـر'),  # Ra
    0x0632: ('ز', None,  None,  'ـز'),  # Zay
    0x0633: ('س', 'سـ', 'ـسـ', 'ـس'),  # Sin
    0x0634: ('ش', 'شـ', 'ـشـ', 'ـش'),  # Shin
    0x0635: ('ص', 'صـ', 'ـصـ', 'ـص'),  # Sad
    0x0636: ('ض', 'ضـ', 'ـضـ', 'ـض'),  # Dad
    0x0637: ('ط', 'طـ', 'ـطـ', 'ـط'),  # Ta
    0x0638: ('ظ', 'ظـ', 'ـظـ', 'ـظ'),  # Dha
    0x0639: ('ع', 'عـ', 'ـعـ', 'ـع'),  # Ain
    0x063A: ('غ', 'غـ', 'ـغـ', 'ـغ'),  # Ghain
    0x0641: ('ف', 'فـ', 'ـفـ', 'ـف'),  # Fa
    0x0642: ('ق', 'قـ', 'ـقـ', 'ـق'),  # Qaf
    0x0643: ('ك', 'كـ', 'ـكـ', 'ـك'),  # Kaf
    0x0644: ('ل', 'لـ', 'ـلـ', 'ـل'),  # Lam
    0x0645: ('م', 'مـ', 'ـمـ', 'ـم'),  # Mim
    0x0646: ('ن', 'نـ', 'ـنـ', 'ـن'),  # Nun
    0x0647: ('ه', 'هـ', 'ـهـ', 'ـه'),  # Ha
    0x0648: ('و', None,  None,  'ـو'),  # Waw
    0x064A: ('ي', 'يـ', 'ـيـ', 'ـي'),  # Ya
    0x0629: ('ة', None,  None,  'ـة'),  # Ta Marbuta
    0x0649: ('ى', None,  None,  'ـى'),  # Alef Maqsura
    0x0622: ('آ', None,  None,  'آ'),  # Alef Madda
    0x0623: ('أ', None,  None,  'أ'),  # Alef Hamza Above
    0x0624: ('ؤ', None,  None,  'ـؤ'),  # Waw Hamza
    0x0625: ('إ', None,  None,  'إ'),  # Alef Hamza Below
    0x0626: ('ئ', 'ئـ', 'ـئـ', 'ـئ'),  # Ya Hamza
}
_NON_JOINING = {0x0627,0x062F,0x0630,0x0631,0x0632,0x0648,0x0629,0x0649,0x0622,0x0623,0x0625}

def _reshape_fallback(text: str) -> str:
    """
    Pure-Python Arabic reshaper — connects letters in the correct contextual form.
    Used only when arabic-reshaper package is unavailable.
    """
    words = text.split(' ')
    result_words = []
    for word in words:
        chars = list(word)
        out = []
        for i, ch in enumerate(chars):
            cp = ord(ch)
            if cp not in _ARABIC_FORMS:
                out.append(ch)
                continue
            iso, ini, med, fin = _ARABIC_FORMS[cp]
            prev_joins = (i > 0 and ord(chars[i-1]) in _ARABIC_FORMS
                          and ord(chars[i-1]) not in _NON_JOINING)
            next_joins = (i < len(chars)-1 and ord(chars[i+1]) in _ARABIC_FORMS
                          and cp not in _NON_JOINING)
            if prev_joins and next_joins and med:
                out.append(med)
            elif prev_joins and fin:
                out.append(fin)
            elif next_joins and ini:
                out.append(ini)
            else:
                out.append(iso)
        # Reverse the word for RTL display
        result_words.append(''.join(reversed(out)))
    # Reverse word order too
    return ' '.join(reversed(result_words))


def is_rtl(text: str) -> bool:
    return bool(_RTL_RE.search(text))


def shape_text(text: str) -> str:
    """Reshape + BiDi for Arabic/Hebrew/Persian. Uses library or pure-Python fallback."""
    if not is_rtl(text):
        return text
    if HAS_BIDI:
        try:
            reshaped = arabic_reshaper.reshape(text)
            return bidi_display(reshaped)
        except Exception:
            pass
    return _reshape_fallback(text)


# ================
# Fonts (multi-script: Latin / Arabic / CJK, with weights)
# ================

# CJK: Chinese, Japanese, Korean, plus halfwidth/fullwidth forms
_CJK_RE = re.compile(
    r'[\u3000-\u303F\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF'
    r'\uAC00-\uD7AF\uFF00-\uFFEF\u3100-\u312F]'
)

_THAI_RE = re.compile(r'[\u0E00-\u0E7F]')
_DEVA_RE = re.compile(r'[\u0900-\u097F]')

def _detect_script(text: str) -> str:
    """Return 'arabic', 'cjk', or 'latin' based on what's in the text."""
    if _RTL_RE.search(text):  return 'arabic'
    if _CJK_RE.search(text):  return 'cjk'
    if _THAI_RE.search(text): return 'thai'
    if _DEVA_RE.search(text): return 'devanagari'
    return 'latin'


class FontCache:
    """Per-script font cache with regular + bold weights."""

    # Search order: bundled in fonts/ → CONFIG_DIR → CWD → system
    _CANDIDATES = {
        ('latin', 'regular'): [
            _resource_path("fonts/SpotifyMix-Medium.ttf"),
            _resource_path("fonts/Inter-Regular.ttf"),
            "SpotifyMix-Medium.ttf",
        ],
        ('latin', 'bold'): [
            _resource_path("fonts/SpotifyMix-Bold.ttf"),
            _resource_path("fonts/Inter-Bold.ttf"),
            "SpotifyMix-Bold.ttf",
        ],
        ('arabic', 'regular'): [
            _resource_path("fonts/Amiri-Regular.ttf"),
            _resource_path("fonts/NotoNaskhArabic-Regular.ttf"),
            os.path.join(CONFIG_DIR, "Amiri-Regular.ttf"),
            "Amiri-Regular.ttf",
            "/System/Library/Fonts/Supplemental/GeezaPro.ttc",
            "C:/Windows/Fonts/tahoma.ttf",
            "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        ],
        ('arabic', 'bold'): [
            _resource_path("fonts/Amiri-Bold.ttf"),
            _resource_path("fonts/NotoNaskhArabic-Bold.ttf"),
            os.path.join(CONFIG_DIR, "Amiri-Bold.ttf"),
            "Amiri-Bold.ttf",
            "C:/Windows/Fonts/tahomabd.ttf",
            "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
        ],
        ('cjk', 'regular'): [
            _resource_path("fonts/NotoSansSC-Regular.ttf"),
            _resource_path("fonts/NotoSansTC-Regular.ttf"),
            _resource_path("fonts/NotoSansJP-Regular.ttf"),
            _resource_path("fonts/NotoSansKR-Regular.ttf"),
            "C:/Windows/Fonts/msyh.ttc",      # Microsoft YaHei (Chinese)
            "C:/Windows/Fonts/msgothic.ttc",  # MS Gothic (Japanese)
            "C:/Windows/Fonts/malgun.ttf",    # Malgun Gothic (Korean)
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
        ],
        ('cjk', 'bold'): [
            _resource_path("fonts/NotoSansSC-Bold.ttf"),
            _resource_path("fonts/NotoSansTC-Bold.ttf"),
            _resource_path("fonts/NotoSansJP-Bold.ttf"),
            _resource_path("fonts/NotoSansKR-Bold.ttf"),
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/msgothic.ttc",
            "/System/Library/Fonts/PingFang.ttc",
        ],
        ('thai', 'regular'): [
            _resource_path("fonts/NotoSansThai-Regular.ttf"),
        ],
        ('thai', 'bold'): [
            _resource_path("fonts/NotoSansThai-Bold.ttf"),
        ],
        ('devanagari', 'regular'): [
            _resource_path("fonts/NotoSansDevanagari-Regular.ttf"),
        ],
        ('devanagari', 'bold'): [
            _resource_path("fonts/NotoSansDevanagari-Bold.ttf"),
        ],
    }

    def __init__(self):
        self.cache: dict = {}
        self.paths: dict = {}
        for key, candidates in self._CANDIDATES.items():
            self.paths[key] = self._first_existing(candidates)
            if self.paths[key]:
                log.info(f"Font {key}: {self.paths[key]}")
            else:
                log.warning(f"Font {key}: NONE found — will use system fallback")

    @staticmethod
    def _first_existing(paths):
        for p in paths:
            if p and os.path.exists(p):
                return p
        return None

    def get(self, weight: str, size: int, script: str = 'latin') -> pygame.font.Font:
        """weight: 'regular' or 'bold'. script: 'latin', 'arabic', 'cjk'."""
        size = max(8, int(round(size)))
        ck = (script, weight, size)
        if ck in self.cache:
            return self.cache[ck]

        # Fallback chain: requested → script's regular → latin same weight → latin regular
        path = (
            self.paths.get((script, weight))
            or self.paths.get((script, 'regular'))
            or self.paths.get(('latin', weight))
            or self.paths.get(('latin', 'regular'))
        )

        if path:
            font = pygame.font.Font(path, size)
        else:
            font = pygame.font.SysFont("Arial", size, bold=(weight == 'bold'))

        self.cache[ck] = font
        return font


FONTS = FontCache()


# =========================
# FIX #4: Improved color helpers
# =========================

def luminance(r: int, g: int, b: int) -> float:
    """Perceptual luminance 0-1."""
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def is_background_dark(colors) -> bool:
    if not colors:
        return True
    total = sum(luminance(*c) for c in colors)
    return (total / len(colors)) < 0.5


def get_most_distinct_colors(image: Image.Image) -> list[tuple]:
    """Original vibrant complementary color extractor."""
    try:
        img = image.convert("RGB").resize((100, 100))
        pixels = np.array(img).reshape(-1, 3)

        vibrant = []
        for r, g, b in pixels:
            rn, gn, bn = r/255, g/255, b/255
            cmax, cmin = max(rn, gn, bn), min(rn, gn, bn)
            s = cmax - cmin if cmax > 0 else 0
            v = cmax
            # Look for highly saturated, non-black/non-white colors
            if s > 0.2 and 0.3 < v < 0.9:
                vibrant.append((r, g, b))

        if not vibrant:
            avg = np.mean(pixels) / 255
            return [(30,30,30),(200,200,200)] if avg < 0.5 else [(240,240,240),(50,50,50)]

        unique = np.unique(np.array(vibrant), axis=0)
        if len(unique) < 2:
            return [(200,50,50),(50,50,200)]

        # Find the two colors furthest apart on the color wheel
        hsv = [colorsys.rgb_to_hsv(c[0]/255, c[1]/255, c[2]/255) for c in unique]
        maxd, c1, c2 = -1, None, None
        for i in range(len(unique)):
            for j in range(i+1, len(unique)):
                h1, s1, _ = hsv[i]
                h2, s2, _ = hsv[j]
                hue_d = min(abs(h1-h2), 1-abs(h1-h2))
                distance = hue_d * (1 + ((s1 + s2) / 2) * 2)
                if distance > maxd:
                    maxd, c1, c2 = distance, tuple(unique[i]), tuple(unique[j])
        return [c1, c2]
    except Exception as e:
        log.error(f"Color extraction error: {e}")
        return [(200,50,50),(50,50,200)]


def hls_adjust(rgb: tuple, lightness: float | None = None, saturation: float | None = None) -> tuple:
    r, g, b = [c / 255.0 for c in rgb]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if lightness is not None:
        l = max(0.0, min(1.0, lightness))
    if saturation is not None:
        s = max(0.0, min(1.0, saturation))
    r2, g2, b2 = colorsys.hls_to_rgb(h, l, s)
    return (int(r2 * 255), int(g2 * 255), int(b2 * 255))


def ensure_contrast(color: tuple, bg_colors: list, min_diff: float = 0.35) -> tuple:
    """
    FIX #4: nudge color lightness until it has enough contrast vs background.
    """
    bg_lum = sum(luminance(*c) for c in bg_colors) / max(1, len(bg_colors))
    fg_lum = luminance(*color)
    diff = abs(fg_lum - bg_lum)
    if diff >= min_diff:
        return color
    # Push toward white or black depending on which direction gives more room
    if bg_lum < 0.5:
        target_l = min(0.92, bg_lum + min_diff + 0.05)
    else:
        target_l = max(0.08, bg_lum - min_diff - 0.05)
    return hls_adjust(color, lightness=target_l)


def create_rounded_album_art(
    pil_image: Image.Image,
    display_size: tuple = (300, 300),
    high_res_size: int = 500,
) -> pygame.Surface:
    try:
        img = pil_image.resize((high_res_size, high_res_size), Image.LANCZOS)
        corner_radius = high_res_size // 14
        mask = Image.new("L", img.size, 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle([(0, 0), img.size], corner_radius, fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(1))
        result = img.copy().convert("RGBA")
        result.putalpha(mask)
        result = result.resize(display_size, Image.LANCZOS)
        return pygame.image.frombytes(result.tobytes(), result.size, result.mode)
    except Exception as e:
        log.error(f"Rounded art error: {e}")
        fallback = pil_image.resize(display_size, Image.LANCZOS)
        return pygame.image.frombytes(fallback.tobytes(), fallback.size, fallback.mode)


# =====================
# Background rendering
# =====================

# Background quality presets  — (compute_w, compute_h, refresh_interval_s)
# Performance: 60×34  @ 24fps refresh  — silky smooth, minimal CPU
# Medium:      120×68 @ 30fps refresh  — balanced (default)
# Detailed:    240×135@ 60fps refresh  — full quality, matches display
_BG_QUALITY_PRESETS = {
    "Performance": (60,  34,  1/24),
    "Medium":      (120, 68,  1/30),
    "Detailed":    (240, 135, 1/60),
}

def _get_bg_params():
    q = prefs.get("bg_quality", "Medium")
    return _BG_QUALITY_PRESETS.get(q, _BG_QUALITY_PRESETS["Medium"])

# Meshgrid cache — rebuilt only when quality setting changes
_bg_grid_cache: dict = {"q": None, "NX": None, "NY": None}
_bg_cache: dict = {"surf": None, "t_last": -999.0, "key": None}

def _ensure_bg_grid():
    q = prefs.get("bg_quality", "Medium")
    if _bg_grid_cache["q"] != q:
        w, h, _ = _BG_QUALITY_PRESETS.get(q, _BG_QUALITY_PRESETS["Medium"])
        _bg_grid_cache["NX"], _bg_grid_cache["NY"] = np.meshgrid(
            np.arange(w) * 0.01, np.arange(h) * 0.01
        )
        _bg_grid_cache["q"] = q
    return _bg_grid_cache["NX"], _bg_grid_cache["NY"]


import queue as _queue

# Single persistent background worker — never re-spawned, no thread overhead
_bg_req_q:  _queue.Queue = _queue.Queue(maxsize=1)
_bg_res_q:  _queue.Queue = _queue.Queue(maxsize=1)


def _bg_persistent_worker():
    while True:
        try:
            job = _bg_req_q.get(timeout=1.0)
        except _queue.Empty:
            continue
        try:
            BG_NX, BG_NY, colors, t, W, H, theme, cache_key = job
            if theme == "Minimal":
                speed, amp1, amp2, amp3 = 0.07, 0.18, 0.08, 0.05
            elif theme == "Neon":
                speed, amp1, amp2, amp3 = 0.25, 0.45, 0.35, 0.25
            else:
                speed, amp1, amp2, amp3 = 0.15, 0.30, 0.20, 0.10
            nt = t * speed
            wave = (
                np.sin(BG_NX + BG_NY * 0.5 + nt) * amp1
                + np.cos(BG_NX * 0.7 - BG_NY + nt * 1.2) * amp2
                + np.sin(BG_NX * 1.5 + BG_NY * 2.0 + nt * 0.8) * amp3
            )
            gpos = np.clip((np.sin(BG_NX * 0.002 + wave) + 1) * 0.5, 0.0, 1.0)
            c0 = np.array(colors[0], dtype=float)
            c1 = np.array(colors[1], dtype=float)
            r = (c0[0] * (1 - gpos) + c1[0] * gpos).astype(np.uint8)
            g = (c0[1] * (1 - gpos) + c1[1] * gpos).astype(np.uint8)
            b = (c0[2] * (1 - gpos) + c1[2] * gpos).astype(np.uint8)
            rgb = np.dstack((r, g, b)).astype(np.uint8)
            small = pygame.surfarray.make_surface(np.transpose(rgb, (1, 0, 2)))
            scaled = pygame.transform.smoothscale(small, (W, H))
            try: _bg_res_q.get_nowait()   # discard stale result
            except _queue.Empty: pass
            _bg_res_q.put((cache_key, scaled))
        except Exception as e:
            log.error(f"BG worker error: {e}")


threading.Thread(target=_bg_persistent_worker, daemon=True).start()

def draw_auth_error_banner(W: int, H: int, scale: float, msg: str):
    h = max(36, int(48 * scale))
    banner = pygame.Surface((W, h), pygame.SRCALPHA)
    banner.fill((180, 50, 50, 230))
    screen.blit(banner, (0, 0))
    font = FONTS.get("bold", max(13, int(18 * scale)))
    surf = font.render(msg, True, (255, 255, 255))
    screen.blit(surf, surf.get_rect(center=(W // 2, h // 2)))

def draw_smooth_fluid_background(
    colors: tuple | list,
    t: float,
    W: int,
    H: int,
    theme: str,
) -> None:
    """
    Persistent-worker background: one long-lived thread, queue-based handoff.
    Main loop never stalls — always blits the last completed frame.
    """
    BG_NX, BG_NY = _ensure_bg_grid()
    _, _, interval = _get_bg_params()
    cache_key = (theme, tuple(colors[0]), tuple(colors[1]), W, H,
                 prefs.get("bg_quality", "Performance"))

    # Collect result if worker finished
    try:
        ck, surf = _bg_res_q.get_nowait()
        _bg_cache["surf"] = surf
        _bg_cache["t_last"] = t
        _bg_cache["key"] = ck
    except _queue.Empty:
        pass

    # Submit new job if due and worker is free
    needs = (
        _bg_cache["surf"] is None
        or t - _bg_cache["t_last"] >= interval
        or _bg_cache["key"] != cache_key
    )
    if needs and _bg_req_q.empty():
        try:
            _bg_req_q.put_nowait(
                (BG_NX.copy(), BG_NY.copy(), colors, t, W, H, theme, cache_key)
            )
        except _queue.Full:
            pass

    if _bg_cache["surf"] is not None:
        screen.blit(_bg_cache["surf"], (0, 0))
    else:
        screen.fill(tuple(colors[0]))


# =================
# Lyrics parsing & sources
# =================

def get_lrclib_lyrics(track_name: str, artist_name: str) -> str | None:
    try:
        r = requests.get(
            "https://lrclib.net/api/get",
            params={"track_name": track_name, "artist_name": artist_name},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("syncedLyrics")
    except Exception as e:
        log.error(f"LRCLib error: {e}")
    return None


def get_unsynced_lyrics_fallback(track_name: str, artist_name: str) -> str | None:
    try:
        r = requests.get(
            f"https://api.lyrics.ovh/v1/{artist_name}/{track_name}",
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("lyrics")
    except Exception as e:
        log.error(f"Unsynced fallback error: {e}")
    return None


def parse_lyrics(text: str) -> list[tuple[float, str]]:
    if not text:
        return []
    parsed = []
    for line in text.splitlines():
        m = re.search(r'\[(\d+):(\d+)(?:\.(\d+))?\]', line)
        if not m:
            continue
        minutes, sec, ms = m.groups()
        ms = ms or "0"
        t = int(minutes) * 60 + int(sec) + int(ms) / 100
        lyric = re.sub(r'\[\d+:\d+\.?\d*\]', '', line).strip()
        if lyric:
            parsed.append((t, lyric))
    return parsed


def auto_time_unsynced(unsynced_text: str, duration_ms: int) -> list[tuple[float, str]]:
    if not unsynced_text:
        return []
    lines = [ln.strip() for ln in unsynced_text.splitlines() if ln.strip()]
    if not lines:
        return []
    total_s = max(5, int(duration_ms / 1000)) if duration_ms else len(lines) * 3
    step = max(1.5, total_s / max(1, len(lines)))
    result = []
    t = 0.0
    for ln in lines:
        result.append((t, ln))
        t += step
    return result


def wrap_text(text: str, font: pygame.font.Font, max_width: int) -> list[str]:
    """Word-wrap. Passes raw text through — shaping happens at render time."""
    words = text.split()
    if not words:
        return []
    lines, cur = [], []
    for w in words:
        test = ' '.join(cur + [w])
        if font.size(test)[0] <= max_width:
            cur.append(w)
        else:
            if cur:
                lines.append(' '.join(cur))
            cur = [w]
    if cur:
        lines.append(' '.join(cur))
    return lines


# ===========================
# FIX #5: Clipping surface for smooth lyric scroll-in/out
# ===========================
# We render lyrics onto a clipping surface so lines fade/slide in from
# outside the viewport rather than popping in/out.

class SmoothScroller:
    """Under-damped spring scroller."""

    def __init__(self):
        self.pos = 0.0
        self.vel = 0.0
        self.k = 110.0
        self.c = 20.0
        self.mass = 1.0

    def snap_to(self, target: float):
        self.pos = float(target)
        self.vel = 0.0

    def update(self, target: float, dt: float) -> float:
        dt = max(0.0, min(dt, 0.04))
        a = (self.k * (target - self.pos) - self.c * self.vel) / self.mass
        self.vel += a * dt
        self.pos += self.vel * dt
        return self.pos


scroller = SmoothScroller()


def draw_text_with_motion_blur(
    surface: pygame.Surface,
    font: pygame.font.Font,
    text: str,
    pos: tuple,
    color: tuple,
    blur: int = 1,
    spread: int = 1,
    rtl: bool = False,
    max_width: int = 9999,
    pre_shaped: bool = False,
) -> None:
    """
    Render text with optional faux motion blur. Aligns RTL text to the right.
    Shape happens ONCE here — never call shape_text before passing text in.
    Set pre_shaped=True only if text was already shaped externally.
    """
    x, y = pos
    # Shape exactly once
    display_text = text if (not rtl or pre_shaped) else shape_text(text)
    base = font.render(display_text, True, color)

    if rtl:
        x = max(0, max_width - base.get_width())

    if blur > 0:
        shadow = font.render(display_text, True, (*color[:3], 70))
        shadow.set_alpha(70)
        for dx, dy in [(-spread, 0), (spread, 0)]:
            surface.blit(shadow, (x + dx, y + dy))

    surface.blit(base, (x, y))


# ============================
# Spotify polling (background thread)
# ============================

def spotify_sync():
    global current_lyrics, current_song_id, sync_progress_ms, sync_timestamp
    global is_playing, album_art_surface, current_artist, current_song_name
    global dominant_colors, track_duration_ms, is_loading, loading_started_at
    global auth_error

    consecutive_fails = 0

    while True:
        try:
            cur = sp.currently_playing()
            consecutive_fails = 0
            auth_error = None
        except spotipy.SpotifyOauthError as e:
            log.error(f"Spotify OAuth error: {e}")
            auth_error = "Spotify login expired. Please restart the app."
            time.sleep(15)
            continue
        except spotipy.SpotifyException as e:
            log.error(f"Spotify API error ({e.http_status}): {e}")
            if e.http_status in (401, 403):
                auth_error = "Spotify auth rejected. Please restart the app."
                time.sleep(15)
            else:
                consecutive_fails += 1
                time.sleep(min(3 * consecutive_fails, 30))
            continue
        except Exception as e:
            consecutive_fails += 1
            log.error(f"Spotify connection error: {e}")
            if consecutive_fails >= 5:
                auth_error = "Can't reach Spotify. Check your internet."
            time.sleep(min(3 * consecutive_fails, 30))
            continue


        try:
            if cur and cur.get('item'):
                is_playing = cur.get('is_playing', False)
                sync_progress_ms = cur.get('progress_ms', 0) or 0
                sync_timestamp = time.time() * 1000

                song_id = cur['item']['id']
                if song_id != current_song_id:
                    is_loading = True
                    loading_started_at = time.time()

                    # 1. Fetch data into TEMPORARY variables first
                    new_song_name = cur['item']['name']
                    artists = cur['item'].get('artists') or []
                    new_artist = artists[0]['name'] if artists else ""
                    new_duration = cur['item'].get('duration_ms', 0) or 0

                    new_art_surface = None
                    new_dominant_colors = [(40, 40, 40), (200, 200, 200)]

                    # --- Album Art Logic ---
                    images = cur['item'].get('album', {}).get('images', [])
                    if images:
                        def _art_score(im):
                            w = im.get('width') or 0
                            h = im.get('height') or 0
                            squareness = 1.0 - abs(w - h) / max(w, h, 1)
                            return w * squareness

                        best_image = max(images, key=_art_score)
                        try:
                            resp = requests.get(best_image['url'], timeout=10)
                            resp.raise_for_status()
                            img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                            new_dominant_colors = get_most_distinct_colors(img)
                            new_art_surface = create_rounded_album_art(img, display_size=(300, 300))
                        except Exception as e:
                            log.error(f"Album art error: {e}")

                    # --- Lyrics Fetching Logic ---
                    new_lyrics = []
                    lrc_text = None
                    unsynced = None

                    local_path = os.path.join(LRC_DIR, f"{song_id}.lrc")
                    if os.path.exists(local_path):
                        try:
                            with open(local_path, "r", encoding="utf-8") as f:
                                lrc_text = f.read()
                        except Exception as e:
                            log.error(f"Read local lrc error: {e}")

                    if not lrc_text and prefs.get("use_cache", True):
                        lrc_text = lyric_cache_disk.get(song_id)

                    if not lrc_text:
                        lrc_text = get_lrclib_lyrics(new_song_name, new_artist)

                    if not lrc_text:
                        unsynced = get_unsynced_lyrics_fallback(new_song_name, new_artist)

                    # --- Parsing Lyrics ---
                    if lrc_text:
                        new_lyrics = parse_lyrics(lrc_text)
                        if prefs.get("use_cache", True):
                            lyric_cache_disk[song_id] = lrc_text
                            persist_cache()
                    elif unsynced:
                        new_lyrics = auto_time_unsynced(unsynced, new_duration)

                    # 2. Apply all updates to global state AT THE EXACT SAME TIME
                    current_song_name = new_song_name
                    current_artist = new_artist
                    track_duration_ms = new_duration
                    album_art_surface = new_art_surface
                    dominant_colors = new_dominant_colors
                    current_lyrics = new_lyrics

                    # 3. Update the ID LAST. This is the trigger for the main loop.
                    current_song_id = song_id
                    is_loading = False

            else:
                time.sleep(1.5)

        except Exception as e:
            log.error(f"Sync loop error: {e}")

        time.sleep(2)


threading.Thread(target=spotify_sync, daemon=True).start()

# ==============
# Menu UI
# ==============
menu_open = False
menu_index = 0
menu_items = [
    "Theme",
    "Color Mode",
    "User Color",
    "Lyric Offset",
    "FPS",
    "BG Quality",
    "Use Cache",
    "Help",
]

_FPS_OPTIONS = [30, 60, 75, 120, 144, 165, 240]

def cycle_fps(direction: int = 1):
    cur = prefs.get("fps", 60)
    try:
        idx = _FPS_OPTIONS.index(cur)
    except ValueError:
        idx = 1  # default to 60
    prefs["fps"] = _FPS_OPTIONS[(idx + direction) % len(_FPS_OPTIONS)]
    persist_prefs()

_BG_QUALITY_OPTIONS = ["Performance", "Medium", "Detailed"]

def cycle_bg_quality(direction: int = 1):
    cur = prefs.get("bg_quality", "Medium")
    try:
        idx = _BG_QUALITY_OPTIONS.index(cur)
    except ValueError:
        idx = 1
    prefs["bg_quality"] = _BG_QUALITY_OPTIONS[(idx + direction) % len(_BG_QUALITY_OPTIONS)]
    # Clear bg cache so new quality takes effect immediately
    _bg_cache["surf"] = None
    _bg_cache["key"] = None
    persist_prefs()
user_colors = [
    (255, 230, 120),
    (120, 200, 255),
    (255, 140, 200),
    (160, 255, 180),
    (255, 190, 120),
    (200, 200, 255),
]


def next_theme():
    order = ["Normal", "Minimal", "Neon"]
    i = order.index(prefs["theme"]) if prefs["theme"] in order else 0
    prefs["theme"] = order[(i + 1) % len(order)]
    persist_prefs()


def prev_theme():
    order = ["Normal", "Minimal", "Neon"]
    i = order.index(prefs["theme"]) if prefs["theme"] in order else 0
    prefs["theme"] = order[(i - 1) % len(order)]
    persist_prefs()


def toggle_color_mode():
    prefs["use_album_colors"] = not prefs.get("use_album_colors", True)
    persist_prefs()


def cycle_user_color(direction: int = 1):
    uc = tuple(prefs.get("user_color", [255, 230, 120]))
    try:
        idx = user_colors.index(uc)
    except ValueError:
        idx = 0
    prefs["user_color"] = list(user_colors[(idx + direction) % len(user_colors)])
    persist_prefs()


def adjust_offset(delta_ms: int):
    prefs["lyric_offset_ms"] = int(prefs.get("lyric_offset_ms", 0)) + delta_ms
    persist_prefs()


def toggle_cache():
    prefs["use_cache"] = not prefs.get("use_cache", True)
    persist_prefs()


def draw_menu_overlay(W: int, H: int, scale: float, lyric_font, small_font):
    current = pygame.display.get_surface().copy()
    blur_scale = max(1, int(12 * scale))
    tmp_w, tmp_h = max(32, W // blur_scale), max(18, H // blur_scale)
    blurred = pygame.transform.smoothscale(
        pygame.transform.smoothscale(current, (tmp_w, tmp_h)), (W, H)
    )
    screen.blit(blurred, (0, 0))

    dark = pygame.Surface((W, H), pygame.SRCALPHA)
    dark.fill((0, 0, 0, 160))
    screen.blit(dark, (0, 0))

    title_surf = FONTS.get("bold", int(44 * scale)).render("MENU", True, (255, 255, 255))
    screen.blit(title_surf, title_surf.get_rect(center=(W // 2, int(0.14 * H))))

    values = {
        "Theme": prefs.get("theme", "Normal"),
        "Color Mode": "Album" if prefs.get("use_album_colors", True) else "User",
        "User Color": str(tuple(prefs.get("user_color", [255, 230, 120]))),
        "Lyric Offset": f"{int(prefs.get('lyric_offset_ms', 0))} ms",
        "FPS": f"{prefs.get('fps', 60)} Hz",
        "BG Quality": prefs.get("bg_quality", "Medium"),
        "Use Cache": "On" if prefs.get("use_cache", True) else "Off",
        "Help": "Drag & drop .lrc for current track",
    }

    y = int(0.22 * H)
    step = int(44 * scale)
    for idx, label in enumerate(menu_items):
        color = (255, 255, 255) if idx == menu_index else (210, 210, 210)
        surf = lyric_font.render(f"{label}: {values[label]}", True, color)
        screen.blit(surf, surf.get_rect(center=(W // 2, y)))
        y += step

    hint = small_font.render("↑/↓ select • ←/→ change • M close", True, (220, 220, 220))
    screen.blit(hint, hint.get_rect(center=(W // 2, int(0.92 * H))))


# ==============
# Loading overlay
# ==============

def draw_loading_overlay(W: int, H: int, scale: float, t_elapsed: float):
    current = pygame.display.get_surface().copy()
    blur_scale = max(1, int(18 * scale))
    tmp_w, tmp_h = max(16, W // blur_scale), max(10, H // blur_scale)
    blurred = pygame.transform.smoothscale(
        pygame.transform.smoothscale(current, (tmp_w, tmp_h)), (W, H)
    )
    screen.blit(blurred, (0, 0))

    dark = pygame.Surface((W, H), pygame.SRCALPHA)
    dark.fill((0, 0, 0, 180))
    screen.blit(dark, (0, 0))

    cx = W // 2
    base_y = int(0.55 * H)
    dot_r = max(6, int(10 * scale))
    spacing = max(30, int(44 * scale))
    for i in range(3):
        phase = t_elapsed * 5 + i * 0.8
        y_dot = base_y + int(math.sin(phase) * 12 * scale)
        pygame.draw.circle(screen, (240, 240, 240), (cx + (i - 1) * spacing, y_dot), dot_r)

    lbl = FONTS.get("bold", int(28 * scale)).render("Loading…", True, (240, 240, 240))
    screen.blit(lbl, lbl.get_rect(center=(W // 2, int(0.45 * H))))


# ==============
# FIX #2: No-lyrics mode — clean centered album cover, no overlap
# ==============

def draw_no_lyrics_view(W: int, H: int, scale: float, info_color: tuple):
    """Render centered album art + track info. Never draws the top-right art."""
    if not album_art_surface:
        # No art either — just show track name centered
        if current_song_name:
            font = FONTS.get("bold", int(32 * scale))
            s = font.render(current_song_name, True, info_color)
            screen.blit(s, s.get_rect(center=(W // 2, H // 2 - 20)))
            af = FONTS.get("regular", int(22 * scale))
            a = af.render(current_artist, True, info_color)
            screen.blit(a, a.get_rect(center=(W // 2, H // 2 + 24)))
        return

    # Decide cover size: slightly larger than normal since it's the hero element
    cover_size = min(int(min(W, H) * 0.40), 420)
    cover_size = max(120, cover_size)
    art = pygame.transform.smoothscale(album_art_surface, (cover_size, cover_size))
    art_rect = art.get_rect(center=(W // 2, int(H * 0.40)))
    screen.blit(art, art_rect.topleft)

    if current_song_name:
        max_w = min(W - 80, cover_size + 120)
        title_font = FONTS.get("bold", max(16, int(28 * scale)))
        artist_font = FONTS.get("regular", max(13, int(20 * scale)))

        # Wrapped title
        title_lines = wrap_text(current_song_name, title_font, max_w)
        ty = art_rect.bottom + int(18 * scale)
        for i, line in enumerate(title_lines):
            s = title_font.render(line, True, info_color)
            screen.blit(s, s.get_rect(midtop=(W // 2, ty + i * (title_font.get_linesize() + 4))))

        # Artist below title
        ay = ty + len(title_lines) * (title_font.get_linesize() + 4) + int(8 * scale)
        a = artist_font.render(current_artist, True, info_color)
        screen.blit(a, a.get_rect(midtop=(W // 2, ay)))


# ==============
# FIX #1 + #5: Layout constants and clipped lyric viewport
# ==============

def draw_lyrics_view(
    W: int,
    H: int,
    scale: float,
    font_scale: float,
    cover_scale: float,
    current_color: tuple,
    info_color: tuple,
    dim_gray_base: int,
    theme: str,
    dt: float,
    lyric_px: int,
    scroll_pos: float,
    wrapped_cache: list,
    block_heights: list,
    heights_with_spacing: list,
) -> pygame.Rect | None:
    """
    Draw the normal (has-lyrics) layout:
      - Album art top-right
      - Song info below album art
      - Lyrics in a CLIPPED viewport on the left

    FIX #5: lyrics are rendered into a clipping surface so they
    scroll smoothly in from below and out through the top, never
    popping into existence mid-screen.

    Returns the album_rect so callers can reuse it.
    """
    cover_padding = 20

    # --- Album art (top-right) ---
    base_cover = 300
    album_px = int(round(base_cover * cover_scale))
    if min(W, H) < 500:
        album_px = max(120, min(album_px, int(min(W, H) * 0.45)))
    else:
        album_px = max(160, min(album_px, min(W, H) - 40))
    album_rect: pygame.Rect | None = None
    if album_art_surface:
        art_key = (current_song_id, album_px)
        if not hasattr(draw_lyrics_view, "_art_cache") or draw_lyrics_view._art_cache[0] != art_key:
            draw_lyrics_view._art_cache = (art_key, pygame.transform.smoothscale(album_art_surface, (album_px, album_px)))
        album_draw = draw_lyrics_view._art_cache[1]
        album_rect = album_draw.get_rect()
        album_rect.topright = (W - cover_padding, cover_padding)
        screen.blit(album_draw, album_rect.topleft)

    # --- Song info (below album art) ---
    title_px = max(16, min(int(28 * font_scale), 56))
    artist_px = max(13, min(int(20 * font_scale), 48))
    title_font = FONTS.get("regular", title_px)
    artist_font = FONTS.get("bold", artist_px)
    info_color_local = info_color  # separate from lyric highlight color

    if album_rect and current_song_name:
        max_title_w = album_rect.w
        title_lines = wrap_text(current_song_name, title_font, max_title_w)
        top_y = album_rect.bottom + max(8, int(10 * scale))
        for i, line in enumerate(title_lines):
            s = title_font.render(line, True, info_color_local)
            r = s.get_rect(midtop=(album_rect.centerx, top_y + i * (title_font.get_linesize() + 4)))
            screen.blit(s, r)
        ay = top_y + len(title_lines) * (title_font.get_linesize() + 4) + max(4, int(6 * scale))
        a = artist_font.render(current_artist, True, info_color_local)
        screen.blit(a, a.get_rect(midtop=(album_rect.centerx, ay)))

    # --- Lyric area geometry ---
    left_pad = max(20, int(0.035 * W))
    column_gap = max(24, int(0.04 * W))
    top_margin = int(0.08 * H)       # FIX #1: start lyrics higher for more room
    bottom_margin = int(0.10 * H)

    if album_rect:
        lyrics_right = album_rect.left - column_gap
        max_lyric_width = max(220, lyrics_right - left_pad)
    else:
        max_lyric_width = max(220, W - left_pad - max(24, int(0.04 * W)))

    lyric_font = FONTS.get("regular", lyric_px)
    bold_font = FONTS.get("bold", max(20, min(int(lyric_px * 1.05), 80)))

    LINE_H = max(28, int(round(52 * (lyric_px / 36.0))))
    SPACING = max(10, int(round(22 * (lyric_px / 36.0))))

    # wrapped_cache/block_heights/heights_with_spacing passed in from main loop

    # Current lyric index
    cur_idx = -1
    current_time_ms = (
        sync_progress_ms + (time.time() * 1000 - sync_timestamp)
        if is_playing else sync_progress_ms
    )
    current_time_ms += int(prefs.get("lyric_offset_ms", 0))
    current_time_sec = max(0.0, current_time_ms / 1000.0)
    for i, (tsec, _) in enumerate(current_lyrics):
        if tsec <= current_time_sec:
            cur_idx = i

    # --- FIX #5: render into a clipping surface ---
    # The viewport height = screen height minus margins.
    viewport_h = max(100, H - top_margin - bottom_margin)
    viewport_w = max(220, max_lyric_width + left_pad + 20)

    # Per-line render cache (font.render is expensive — cache by text+color+size)
    if not hasattr(draw_lyrics_view, '_line_cache'):
        draw_lyrics_view._line_cache = {}
        draw_lyrics_view._line_cache_song = None
    if draw_lyrics_view._line_cache_song != id(current_lyrics):
        draw_lyrics_view._line_cache.clear()
        draw_lyrics_view._line_cache_song = id(current_lyrics)

    # Reuse persistent surface — avoids 2.5MB heap allocation every frame
    if (_lyric_surf_cache["surf"] is None or
            _lyric_surf_cache["size"] != (viewport_w, viewport_h)):
        _lyric_surf_cache["surf"] = pygame.Surface((viewport_w, viewport_h), pygame.SRCALPHA)
        _lyric_surf_cache["size"] = (viewport_w, viewport_h)
    lyric_surf = _lyric_surf_cache["surf"]
    lyric_surf.fill((0, 0, 0, 0))

    # Add soft fade at top and bottom edges for a nice slide-in/out feel
    fade_h = min(80, viewport_h // 6)

    blur_amount = 1 if theme != "Minimal" else 0
    bg_dark = is_background_dark(dominant_colors)
    gray_boost = 10 if theme == "Neon" else 0

    # Draw each lyric block onto lyric_surf, offset by scroll_pos
    y_cursor = -scroll_pos + 0.0   # float for sub-pixel precision

    for i, (_, text) in enumerate(current_lyrics):
        lines, rtl, script = wrapped_cache[i]
        block_h = block_heights[i]

        # Only render if potentially visible
        y_top = y_cursor
        y_bot = y_cursor + block_h
        if y_bot > 0 and y_top < viewport_h:
            is_cur = (i == cur_idx)
            if is_cur:
                color = current_color
                if script == 'latin':
                    font = bold_font
                else:
                    font = FONTS.get('bold', lyric_px, script=script)
            else:
                # Calculate the faded color for ALL scripts
                dist = abs(i - cur_idx) if cur_idx >= 0 else 3
                gray_val = max(60, min(210, (dim_gray_base + gray_boost) - dist * 45))
                color = (gray_val, gray_val, gray_val)

                if script == 'latin':
                    font = lyric_font
                else:
                    font = FONTS.get('regular', lyric_px, script=script)

            for j, ln in enumerate(lines):
                yy = int(y_top + j * LINE_H)
                if -LINE_H < yy < viewport_h + LINE_H:
                    ck = (ln, color, lyric_px, rtl, is_cur)
                    line_cache = draw_lyrics_view._line_cache
                    if ck not in line_cache:
                        display = shape_text(ln) if rtl else ln
                        line_cache[ck] = font.render(display, True, color)
                        if len(line_cache) > 500:  # evict oldest 100
                            for k in list(line_cache)[:100]: del line_cache[k]
                    surf_ln = line_cache[ck]
                    blit_x = max(0, max_lyric_width + left_pad - surf_ln.get_width()) if rtl else left_pad
                    lyric_surf.blit(surf_ln, (blit_x, yy))

        y_cursor += heights_with_spacing[i]

    # Fade mask — built once and cached (rebuilding 160 rows per frame is expensive)
    if fade_h > 0:
        fade_key = (viewport_w, viewport_h, fade_h)
        if not hasattr(draw_lyrics_view, "_fade_cache") or draw_lyrics_view._fade_cache[0] != fade_key:
            fade_surf = pygame.Surface((viewport_w, viewport_h), pygame.SRCALPHA)
            fade_surf.fill((255, 255, 255, 255))
            for row in range(fade_h):
                alpha = int(255 * (row / fade_h))
                fade_surf.fill((255, 255, 255, alpha), (0, row, viewport_w, 1))
                fade_surf.fill((255, 255, 255, alpha), (0, viewport_h - 1 - row, viewport_w, 1))
            draw_lyrics_view._fade_cache = (fade_key, fade_surf)
        lyric_surf.blit(draw_lyrics_view._fade_cache[1], (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

    screen.blit(lyric_surf, (0, top_margin))

    return album_rect


# ==============
# Main loop
# ==============
def _invalidate_lyric_caches():
    """Force layout, surface, and line caches to rebuild on next frame.
    Call after any operation that replaces current_lyrics without changing song_id."""
    layout_cache["song_id"] = None
    layout_cache["wrapped_lines"] = []
    layout_cache["block_heights"] = []
    layout_cache["h_with_spacing"] = []
    _lyric_surf_cache["surf"] = None
    if hasattr(draw_lyrics_view, '_line_cache'):
        draw_lyrics_view._line_cache.clear()

running = True
last_time = time.time()
bg_time = 0.0
last_layout_sig = None
_scroller_updated_this_frame = False  # prevent double-update bug
_last_known_song_id = None  # track song changes to clear stale lyrics
_prev_is_loading = False

while running:
    W, H = screen.get_size()
    now = time.time()
    dt = now - last_time
    last_time = now
    bg_time += dt
    # Wrap to keep sin() inputs from growing unbounded in long sessions.
    _BG_WRAP = 41.9  # ~2*pi/0.15 (Normal theme period — safe for all themes)
    if bg_time > _BG_WRAP * 10:
        bg_time = bg_time % _BG_WRAP

    # Clear stale lyrics the instant song_id changes (before any rendering)
    if current_song_id != _last_known_song_id:
        _last_known_song_id = current_song_id
        _invalidate_lyric_caches()

    # --- Events ---
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                if menu_open:
                    menu_open = False
                else:
                    running = False

            elif event.key == pygame.K_m:
                menu_open = not menu_open

            elif event.key == pygame.K_F11 or (
                event.key == pygame.K_RETURN and (event.mod & pygame.KMOD_ALT)
            ):
                ts = time.time()
                if ts - _last_toggle_ts > 0.25:
                    set_window_mode(not is_fullscreen)
                    _last_toggle_ts = ts

            elif menu_open:
                if event.key == pygame.K_UP:
                    menu_index = (menu_index - 1) % len(menu_items)
                elif event.key == pygame.K_DOWN:
                    menu_index = (menu_index + 1) % len(menu_items)
                elif event.key == pygame.K_LEFT:
                    label = menu_items[menu_index]
                    if label == "Theme":          prev_theme()
                    elif label == "Color Mode":   toggle_color_mode()
                    elif label == "User Color":   cycle_user_color(-1)
                    elif label == "Lyric Offset": adjust_offset(-50)
                    elif label == "FPS":          cycle_fps(-1)
                    elif label == "BG Quality":   cycle_bg_quality(-1)
                    elif label == "Use Cache":    toggle_cache()
                elif event.key == pygame.K_RIGHT:
                    label = menu_items[menu_index]
                    if label == "Theme":          next_theme()
                    elif label == "Color Mode":   toggle_color_mode()
                    elif label == "User Color":   cycle_user_color(+1)
                    elif label == "Lyric Offset": adjust_offset(+50)
                    elif label == "FPS":          cycle_fps(+1)
                    elif label == "BG Quality":   cycle_bg_quality(+1)
                    elif label == "Use Cache":    toggle_cache()


        elif event.type == pygame.VIDEORESIZE:
            if not is_fullscreen:
                new_w = max(640, event.w)
                new_h = max(360, event.h)
                windowed_size = [new_w, new_h]
                # You absolutely HAVE to call set_mode to get a new surface size.
                screen = pygame.display.set_mode((new_w, new_h), pygame.RESIZABLE)
                # Re-apply the Windows border hacks since set_mode wipes them out
                _strip_caption()
                did_resize = True

        elif event.type == pygame.DROPFILE:
            path = event.file
            if not current_song_id:
                log.warning("LRC drop ignored: no song is currently playing")
                continue
            if not path.lower().endswith(".lrc"):
                log.warning(f"LRC drop ignored: {path} is not a .lrc file")
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
                save_path = os.path.join(LRC_DIR, f"{current_song_id}.lrc")
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write(text)
                current_lyrics = parse_lyrics(text)
                _invalidate_lyric_caches()  # ← THE FIX
                log.info(f"✓ LRC saved: {save_path}")
            except Exception as e:
                log.error(f"LRC import error: {e}")

        elif event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 1 and not is_fullscreen and not menu_open:
                zone = _hit_test(event.pos[0], event.pos[1], W, H)
                if zone is not None:
                    _native_drag_resize(zone)

    # --- Background ---
    _update_cursor(W, H)
    bg_colors = (
        dominant_colors
        if prefs.get("use_album_colors", True)
        else (tuple(prefs["user_color"]), (25, 25, 25))
    )
    draw_smooth_fluid_background(bg_colors, bg_time, W, H, prefs.get("theme", "Normal"))

    # --- Scale factors ---
    w_ratio = W / 1920.0
    h_ratio = H / 1080.0
    scale = (w_ratio * h_ratio) ** 0.5

    # Compact mode = sidebar-size windows. Below 500px we let things shrink;
    # above that we use the original floors so fullscreen looks normal.
    compact = min(W, H) < 500
    if compact:
        font_scale = scale * 1.10
        cover_scale = scale * 1.00
        lyric_px = max(18, min(int(round(36 * font_scale)), 72))
    else:
        font_scale = max(0.90, scale * 1.10)
        cover_scale = max(0.95, scale * 1.00)
        lyric_px = max(20, min(int(round(36 * font_scale)), 72))

    # --- Restore original tinted text colors ---
    if prefs.get("use_album_colors", True):
        base_accent = dominant_colors[0]
    else:
        base_accent = tuple(prefs.get("user_color", [255, 230, 120]))

    bg_dark = is_background_dark(dominant_colors)

    # Tint the lyrics to match the vibe of the cover art
    if bg_dark:
        current_color = hls_adjust(base_accent, lightness=0.84, saturation=0.90)
        info_color = hls_adjust(base_accent, lightness=0.78, saturation=0.80)
        dim_gray_base = 185
    else:
        current_color = hls_adjust(base_accent, lightness=0.28, saturation=0.90)
        info_color = hls_adjust(base_accent, lightness=0.35, saturation=0.80)
        dim_gray_base = 70

    # --- Compute scroll target (shared between modes) ---
    has_lyrics = len(current_lyrics) > 0

    if has_lyrics:
        # Recompute target scroll for spring
        top_margin = int(0.08 * H)
        bottom_margin = int(0.10 * H)
        visible_h = max(100, H - top_margin - bottom_margin)

        LINE_H = max(28, int(round(52 * (lyric_px / 36.0))))
        SPACING = max(10, int(round(22 * (lyric_px / 36.0))))

        # ── Compute the EXACT same max_lyric_width as draw_lyrics_view uses ──
        # This is critical — if we wrap at a different width here, block heights
        # will differ from what's drawn, and the scroll target will be wrong.
        cover_padding = 20
        base_cover = 300
        _album_px = int(round(base_cover * cover_scale))
        if min(W, H) < 500:
            _album_px = max(120, min(_album_px, int(min(W, H) * 0.45)))
        else:
            _album_px = max(160, min(_album_px, min(W, H) - 40))
        left_pad = max(20, int(0.035 * W))
        column_gap = max(24, int(0.04 * W))
        if album_art_surface:
            # Mirror draw_lyrics_view: album rect topright = (W - cover_padding, cover_padding)
            album_left = (W - cover_padding) - _album_px
            scroll_max_lyric_w = max(220, album_left - column_gap - left_pad)
        else:
            scroll_max_lyric_w = max(220, W - left_pad - max(24, int(0.04 * W)))

        # Only recompute wrapping when song, width, or font changes — NOT every frame
        _lc = layout_cache
        if (_lc["song_id"] != current_song_id
                or _lc["width"] != scroll_max_lyric_w
                or _lc["lyric_px"] != lyric_px
                or _lc["LINE_H"] != LINE_H):
            _wrapped = []
            _heights = []
            for _, text in current_lyrics:
                script = _detect_script(text)
                rtl = (script == 'arabic')
                font_tmp = FONTS.get('regular', lyric_px, script=script)
                lines_tmp = wrap_text(text, font_tmp, scroll_max_lyric_w)
                _wrapped.append((lines_tmp, rtl, script))  # ← now 3-tuple
                _heights.append(len(lines_tmp) * LINE_H)
            _hspacing = [
                h + (SPACING if i < len(_heights) - 1 else 0)
                for i, h in enumerate(_heights)
            ]
            layout_cache.update({
                "song_id": current_song_id,
                "width": scroll_max_lyric_w,
                "lyric_px": lyric_px,
                "LINE_H": LINE_H,
                "SPACING": SPACING,
                "wrapped_lines": _wrapped,
                "block_heights": _heights,
                "h_with_spacing": _hspacing,
            })

        block_heights_tmp = layout_cache["block_heights"]
        h_with_spacing = layout_cache["h_with_spacing"]

        current_time_ms = (
            sync_progress_ms + (time.time() * 1000 - sync_timestamp)
            if is_playing else sync_progress_ms
        )
        current_time_ms += int(prefs.get("lyric_offset_ms", 0))
        current_time_sec = max(0.0, current_time_ms / 1000.0)
        cur_idx = -1
        for i, (tsec, _) in enumerate(current_lyrics):
            if tsec <= current_time_sec:
                cur_idx = i

        if cur_idx >= 0:
            total_before = sum(h_with_spacing[:cur_idx])
            # Anchor = fixed Y position on screen where current line top sits.
            # Using 35% down the visible area keeps it consistently placed
            # regardless of whether the block is 1 line or 4 lines tall.
            anchor = int(visible_h * 0.62)
            # Scroll so the TOP of the current block lands at the anchor point.
            # 0.62 = lower third of the viewport
            target_scroll = max(0.0, total_before - anchor)
        else:
            target_scroll = 0.0

        layout_sig = (W, H, lyric_px, prefs.get("theme"))
        if layout_sig != last_layout_sig or did_resize:
            scroller.snap_to(target_scroll)
            last_layout_sig = layout_sig
        else:
            scroller.update(target_scroll, dt)

        did_resize = False
        scroll_pos = scroller.pos

        # Only draw if cache is populated for the current song
        if (layout_cache["song_id"] == current_song_id
                and len(layout_cache["wrapped_lines"]) == len(current_lyrics)):
            draw_lyrics_view(
                W, H, scale, font_scale, cover_scale,
                current_color, info_color, dim_gray_base,
                prefs.get("theme", "Normal"),
                dt, lyric_px, scroll_pos,
                layout_cache["wrapped_lines"],
                layout_cache["block_heights"],
                layout_cache["h_with_spacing"],
            )

    else:
        # FIX #2: clean no-lyrics layout (no double art)
        scroller.snap_to(0.0)
        did_resize = False
        draw_no_lyrics_view(W, H, scale, info_color)

    if auth_error and not is_loading:
        draw_auth_error_banner(W, H, scale, auth_error)

    # --- Overlays ---
    if menu_open:
        draw_menu_overlay(
            W, H, scale,
            FONTS.get("regular", int(24 * scale)),
            FONTS.get("regular", int(18 * scale)),
        )

    if is_loading:
        draw_loading_overlay(W, H, scale, time.time() - loading_started_at)
        if not _prev_is_loading:  # rising edge: collect once per loading cycle
            gc.collect()
    _prev_is_loading = is_loading

    pygame.display.flip()
    clock.tick(prefs.get('fps', 60))

# Restore Windows timer resolution
if platform.system() == 'Windows':
    try:
        ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass

pygame.quit()
sys.exit()