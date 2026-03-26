"""
Streamy -- YouTube Music client, single-file Python edition.
Run:  python streamy.py
Install dependencies once:
pip install customtkinter Pillow yt-dlp sounddevice numpy ytmusicapi
"""
# ==============================================================================
# IMPORTS
# ==============================================================================
import io
import json
import os
import re
import shutil
import subprocess
import threading
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
import customtkinter as ctk
import numpy as np
import sounddevice as sd
from PIL import Image, ImageDraw, ImageTk
from yt_dlp import YoutubeDL

# ==============================================================================
# RALEWAY FONT SETUP
# ==============================================================================
_FONT_DIR  = os.path.join(os.path.expanduser("~"), ".metroiist_fonts")
_RALEWAY_REGULAR = os.path.join(_FONT_DIR, "Raleway-Regular.ttf")
_RALEWAY_FAMILY: Optional[str] = None

def _setup_raleway() -> Optional[str]:
    os.makedirs(_FONT_DIR, exist_ok=True)
    if os.path.exists(_RALEWAY_REGULAR):
        return _register_font(_RALEWAY_REGULAR)
    urls_to_try = [
        "https://github.com/google/fonts/raw/main/ofl/raleway/Raleway%5Bwght%5D.ttf",
        "https://raw.githubusercontent.com/google/fonts/main/ofl/raleway/static/Raleway-Regular.ttf",
    ]
    for url in urls_to_try:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Streamy/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read()
                if len(data) > 10000:
                    with open(_RALEWAY_REGULAR, "wb") as f:
                        f.write(data)
                    return _register_font(_RALEWAY_REGULAR)
        except Exception:
            continue
    return None

def _register_font(path: str) -> Optional[str]:
    try:
        import ctypes
        if hasattr(ctypes, "windll"):
            ctypes.windll.gdi32.AddFontResourceExW(path, 0x10, 0)
            return "Raleway"
    except Exception:
        pass
    return None

def _init_font_async():
    global _RALEWAY_FAMILY
    try:
        _RALEWAY_FAMILY = _setup_raleway()
    except Exception:
        pass

threading.Thread(target=_init_font_async, daemon=True).start()

def _font(size: int, weight: str = "normal") -> ctk.CTkFont:
    family = _RALEWAY_FAMILY or "Segoe UI"
    return ctk.CTkFont(family=family, size=size,
        weight="bold" if weight == "bold" else "normal")

# ==============================================================================
# MODEL
# ==============================================================================
@dataclass
class Track:
    video_id:      str = ""
    title:         str = ""
    artist:        str = ""
    album:         str = ""
    duration:      str = ""
    thumbnail_url: str = ""
    stream_url:    str = ""
    download_path: str = ""
    
    def display_name(self) -> str:
        return f"{self.artist} -- {self.title}" if self.artist else self.title
    
    def to_dict(self) -> dict:
        return {
            "video_id":      self.video_id,
            "title":         self.title,
            "artist":        self.artist,
            "album":         self.album,
            "duration":      self.duration,
            "thumbnail_url": self.thumbnail_url,
            "download_path": self.download_path,
        }
    
    @staticmethod
    def from_dict(d: dict) -> "Track":
        return Track(
            video_id      = d.get("video_id", ""),
            title         = d.get("title", ""),
            artist        = d.get("artist", ""),
            album         = d.get("album", ""),
            duration      = d.get("duration", ""),
            thumbnail_url = d.get("thumbnail_url", ""),
            download_path = d.get("download_path", ""),
        )

# ==============================================================================
# MATERIAL YOU COLOUR ENGINE
# ==============================================================================
def _clamp(v: int) -> int:
    return max(0, min(255, v))

def _hex(r: int, g: int, b: int) -> str:
    return f"#{_clamp(r):02x}{_clamp(g):02x}{_clamp(b):02x}"

def _lerp(a: int, b: int, t: float) -> int:
    return _clamp(int(a + (b - a) * t))

def _lerp_col(c1: tuple, c2: tuple, t: float) -> tuple:
    return (_lerp(c1[0], c2[0], t), _lerp(c1[1], c2[1], t), _lerp(c1[2], c2[2], t))

def _luminance(r: int, g: int, b: int) -> float:
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255

def _shift_hue(r: int, g: int, b: int, deg: float) -> tuple:
    import colorsys
    h, s, v = colorsys.rgb_to_hsv(r/255, g/255, b/255)
    h = (h + deg / 360) % 1.0
    nr, ng, nb = colorsys.hsv_to_rgb(h, s, v)
    return (int(nr*255), int(ng*255), int(nb*255))

def extract_palette(img: Image.Image) -> Dict[str, str]:
    try:
        small = img.convert("RGB").resize((64, 64), Image.LANCZOS)
        quantized = small.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
        palette_raw = quantized.getpalette()[:8*3]
        colours = [(palette_raw[i*3], palette_raw[i*3+1], palette_raw[i*3+2]) for i in range(8)]
        import colorsys
        def score(c):
            h, s, v = colorsys.rgb_to_hsv(c[0]/255, c[1]/255, c[2]/255)
            lum = _luminance(*c)
            if s < 0.2 or lum < 0.05:
                return 0
            return s * (1 - abs(lum - 0.5))
        seed = max(colours, key=score)
        if score(seed) < 0.05:
            seed = (103, 58, 183)
    except Exception:
        seed = (103, 58, 183)
    sr, sg, sb = seed
    black  = (0, 0, 0)
    white  = (255, 255, 255)
    btn_col     = _lerp_col(seed, white, 0.70)
    btn_on      = (0, 0, 0) if _luminance(*btn_col) > 0.4 else (255, 255, 255)
    lyric_on    = _lerp_col(seed, white, 0.85)
    lyric_off   = _lerp_col(seed, (30, 30, 30), 0.7)
    card        = _lerp_col(seed, (20, 20, 20), 0.92)
    card_hover  = _lerp_col(seed, (35, 35, 35), 0.88)
    return {
        "name":               "dark",
        "bg":                 "#000000",
        "bg_secondary":       "#1c1c1e",
        "bg_hover":           _hex(*card_hover),
        "header_bg":          "#000000",
        "text":               "#ffffff",
        "text_secondary":     "#ebebf5",
        "text_muted":         "#8e8e93",
        "accent_light":       _hex(*btn_col),
        "accent":             _hex(*btn_col),
        "btn_primary":        _hex(*btn_col),
        "btn_text":           _hex(*btn_on),
        "btn_secondary":      _hex(*card),
        "btn_secondary_text": "#ebebf5",
        "player_bg":          "#000000",
        "status_bg":          "#000000",
        "status_text":        "#8e8e93",
        "np_bg":              "#000000",
        "lyric_active":       _hex(*lyric_on),
        "lyric_inactive":     _hex(*lyric_off),
        "card_bg":            _hex(*card),
        "seed_hex":           _hex(sr, sg, sb),
    }

DARK: Dict[str, str] = {
    "name":               "dark",
    "bg":                 "#0d0d14",
    "sidebar_bg":         "#0a0a10",
    "bg_secondary":       "#161620",
    "bg_hover":           "#1e1e2e",
    "header_bg":          "#0d0d14",
    "text":               "#ffffff",
    "text_secondary":     "#b3b3cc",
    "text_muted":         "#6b6b8a",
    "accent_light":       "#c084fc",
    "accent":             "#a855f7",
    "btn_primary":        "#c084fc",
    "btn_text":           "#000000",
    "btn_secondary":      "#1e1e2e",
    "btn_secondary_text": "#c084fc",
    "player_bg":          "#0a0a10",
    "status_bg":          "#0a0a10",
    "status_text":        "#6b6b8a",
    "np_bg":              "#0d0d14",
    "lyric_active":       "#e0aaff",
    "lyric_inactive":     "#3a2a50",
    "card_bg":            "#161620",
    "sidebar_item_active":"#1e1e2e",
    "seed_hex":           "#a855f7",
}

# ==============================================================================
# PLAYLIST MANAGER
# ==============================================================================
_PLAYLISTS_FILE = os.path.join(os.path.expanduser("~"), ".metroiist_playlists.json")
_PLAY_COUNTS_FILE = os.path.join(os.path.expanduser("~"), ".metroiist_playcounts.json")

class PlaylistManager:
    def __init__(self):
        self._playlists: Dict[str, List[Track]] = {"Liked": []}
        self._play_counts: Dict[str, int] = {}
        self._load()
    
    def create_playlist(self, name: str) -> bool:
        if name in self._playlists or not name.strip():
            return False
        self._playlists[name] = []
        self._save()
        return True
    
    def delete_playlist(self, name: str) -> bool:
        if name in self._playlists and name != "Liked":
            del self._playlists[name]
            self._save()
            return True
        return False
    
    def add_to_playlist(self, name: str, track: Track) -> bool:
        if name not in self._playlists:
            return False
        for t in self._playlists[name]:
            if t.video_id == track.video_id:
                return False
        self._playlists[name].append(track)
        self._save()
        return True
    
    def remove_from_playlist(self, name: str, video_id: str) -> bool:
        if name not in self._playlists:
            return False
        self._playlists[name] = [t for t in self._playlists[name] if t.video_id != video_id]
        self._save()
        return True
    
    def get_playlist(self, name: str) -> List[Track]:
        return self._playlists.get(name, [])
    
    def get_all_playlists(self) -> List[str]:
        return list(self._playlists.keys())
    
    def is_in_playlist(self, name: str, video_id: str) -> bool:
        for t in self._playlists.get(name, []):
            if t.video_id == video_id:
                return True
        return False
    
    def increment_play_count(self, video_id: str) -> int:
        self._play_counts[video_id] = self._play_counts.get(video_id, 0) + 1
        count = self._play_counts[video_id]
        self._save()
        return count
    
    def get_play_count(self, video_id: str) -> int:
        return self._play_counts.get(video_id, 0)
    
    def _save(self):
        try:
            with open(_PLAYLISTS_FILE, "w", encoding="utf-8") as f:
                json.dump({name: [t.to_dict() for t in tracks] 
                          for name, tracks in self._playlists.items()}, f, indent=2)
            with open(_PLAY_COUNTS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._play_counts, f, indent=2)
        except Exception:
            pass
    
    def _load(self):
        try:
            with open(_PLAYLISTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                self._playlists = {name: [Track.from_dict(t) for t in tracks] 
                                   for name, tracks in data.items()}
                if "Liked" not in self._playlists:
                    self._playlists["Liked"] = []
        except Exception:
            self._playlists = {"Liked": []}
        try:
            with open(_PLAY_COUNTS_FILE, "r", encoding="utf-8") as f:
                self._play_counts = json.load(f)
        except Exception:
            self._play_counts = {}

# ==============================================================================
# DOWNLOAD MANAGER
# ==============================================================================
_DOWNLOADS_FILE = os.path.join(os.path.expanduser("~"), ".metroiist_downloads.json")
_DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "StreamyDownloads")

class DownloadManager:
    def __init__(self):
        self._downloads: List[Track] = []
        os.makedirs(_DOWNLOADS_DIR, exist_ok=True)
        self._load()
    
    def add_download(self, track: Track, path: str):
        track.download_path = path
        for t in self._downloads:
            if t.video_id == track.video_id:
                return False
        self._downloads.append(track)
        self._save()
        return True
    
    def remove_download(self, video_id: str) -> bool:
        for i, t in enumerate(self._downloads):
            if t.video_id == video_id:
                if os.path.exists(t.download_path):
                    os.remove(t.download_path)
                self._downloads.pop(i)
                self._save()
                return True
        return False
    
    def get_all_downloads(self) -> List[Track]:
        return list(self._downloads)
    
    def is_downloaded(self, video_id: str) -> bool:
        for t in self._downloads:
            if t.video_id == video_id:
                return os.path.exists(t.download_path)
        return False
    
    def _save(self):
        try:
            with open(_DOWNLOADS_FILE, "w", encoding="utf-8") as f:
                json.dump([t.to_dict() for t in self._downloads], f, indent=2)
        except Exception:
            pass
    
    def _load(self):
        try:
            with open(_DOWNLOADS_FILE, "r", encoding="utf-8") as f:
                self._downloads = [Track.from_dict(d) for d in json.load(f)]
        except Exception:
            self._downloads = []

# ==============================================================================
# YOUTUBE SEARCH
# ==============================================================================
def _safe(obj, *keys, default=None):
    for k in keys:
        if obj is None:
            return default
        if isinstance(obj, list):
            if not isinstance(k, int) or k >= len(obj):
                return default
            obj = obj[k]
        elif isinstance(obj, dict):
            obj = obj.get(k)
        else:
            return default
    return obj if obj is not None else default

def _innertube_search(query: str) -> List[Track]:
    url = "https://music.youtube.com/youtubei/v1/search?alt=json&key=AIzaSyC9XL3ZjWddXya6X74dJoCTL-WEYFDNX30"
    payload = json.dumps({
        "context": {"client": {"clientName": "WEB_REMIX", "clientVersion": "1.20240101.01.00", "hl": "en", "gl": "US"}},
        "query": query,
        "params": "EgWKAQIIAWoKEAkQBRAKEAMQBA==",
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "X-YouTube-Client-Name": "67",
        "X-YouTube-Client-Version": "1.20240101.01.00",
        "Origin": "https://music.youtube.com",
        "Referer": "https://music.youtube.com/",
    }
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=12) as r:
        data = json.loads(r.read().decode())
    tracks: List[Track] = []
    tabs = _safe(data, "contents", "tabbedSearchResultsRenderer", "tabs", default=[])
    shelves = _safe(tabs, 0, "tabRenderer", "content", "sectionListRenderer", "contents", default=[])
    for shelf_wrap in shelves:
        shelf = _safe(shelf_wrap, "musicShelfRenderer", default={})
        if not shelf:
            continue
        for item_wrap in _safe(shelf, "contents", default=[]):
            r = _safe(item_wrap, "musicResponsiveListItemRenderer", default={})
            if not r:
                continue
            video_id = _safe(r, "overlay", "musicItemThumbnailOverlayRenderer", "content",
                "musicPlayButtonRenderer", "playNavigationEndpoint", "watchEndpoint", "videoId", default="")
            if not video_id:
                continue
            thumbs = _safe(r, "thumbnail", "musicThumbnailRenderer", "thumbnail", "thumbnails", default=[])
            thumb_url = thumbs[-1].get("url", "") if thumbs else ""
            cols = _safe(r, "flexColumns", default=[])
            title = _safe(cols, 0, "musicResponsiveListItemFlexColumnRenderer", "text", "runs", 0, "text", default="")
            runs1 = _safe(cols, 1, "musicResponsiveListItemFlexColumnRenderer", "text", "runs", default=[])
            artist = runs1[0].get("text", "") if len(runs1) > 0 else ""
            album = runs1[2].get("text", "") if len(runs1) > 2 else ""
            duration = runs1[4].get("text", "") if len(runs1) > 4 else ""
            tracks.append(Track(video_id=video_id, title=title, artist=artist,
                album=album, duration=duration, thumbnail_url=thumb_url))
            if len(tracks) >= 30:
                break
        if tracks:
            break
    return tracks

def search_async(query: str, on_results: Callable, on_error: Callable) -> None:
    def _worker():
        try:
            on_results(_innertube_search(query))
        except Exception as exc:
            on_error(str(exc))
    threading.Thread(target=_worker, daemon=True).start()

# ==============================================================================
# STREAM RESOLVER
# ==============================================================================
def resolve_stream_async(track: Track, on_resolved: Callable, on_error: Callable) -> None:
    def _worker():
        ydl_opts = {
            "quiet": True, "no_warnings": True,
            "format": "bestaudio[ext=webm]/bestaudio[ext=opus]/bestaudio/best",
            "skip_download": True, "noplaylist": True,
        }
        try:
            url = f"https://www.youtube.com/watch?v={track.video_id}"
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    on_error(track.video_id, "Could not extract stream info.")
                    return
                formats = info.get("formats", [])
                stream_url = ""
                for f in formats:
                    if f.get("vcodec") == "none" and f.get("acodec", "").startswith("opus") and f.get("url"):
                        stream_url = f["url"]
                        break
                if not stream_url:
                    audio_only = [f for f in formats if f.get("vcodec") == "none" and f.get("url")]
                    if audio_only:
                        stream_url = max(audio_only, key=lambda f: f.get("abr") or f.get("tbr") or 0)["url"]
                if not stream_url:
                    stream_url = info.get("url") or (formats[-1].get("url") if formats else "")
                if not stream_url:
                    on_error(track.video_id, "No playable stream found.")
                    return
                best_thumb = track.thumbnail_url
                thumbs = info.get("thumbnails") or []
                if thumbs:
                    real = [t for t in thumbs if t.get("url") and "storyboard" not in t.get("url", "") and t.get("width", 0) != 0]
                    if real:
                        best = max(real, key=lambda t: t.get("width", 0) * t.get("height", 0))
                        best_thumb = best.get("url", best_thumb)
                on_resolved(track, stream_url, best_thumb)
        except Exception as exc:
            on_error(track.video_id, str(exc))
    threading.Thread(target=_worker, daemon=True).start()

# ==============================================================================
# FFMPEG FINDER
# ==============================================================================
def _ffmpeg_bin() -> str:
    try:
        from yt_dlp.utils import find_exe
        p = find_exe("ffmpeg")
        if p:
            return p
    except Exception:
        pass
    import platform
    home = os.path.expanduser("~")
    candidates = ([os.path.join(home, "AppData", "Roaming", "yt-dlp", "ffmpeg.exe"),
        os.path.join(home, ".yt-dlp", "ffmpeg.exe")] if platform.system() == "Windows"
        else [os.path.join(home, ".yt-dlp", "ffmpeg"), "/usr/local/bin/ffmpeg"])
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise RuntimeError("ffmpeg not found. Download from https://www.gyan.dev/ffmpeg/builds/")

# ==============================================================================
# AUDIO PLAYER
# ==============================================================================
class AudioPlayer:
    CHUNK_FRAMES = 4096
    SAMPLE_RATE = 48000
    CHANNELS = 2
    
    def __init__(self):
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._pause_evt = threading.Event()
        self._current_url = ""
        self._volume = 80
        self._playing = False
        self._paused = False
        self._pos_s = 0.0
        self._dur_s = 0.0
        self._ffmpeg_proc = None
        self._pos_cb: Optional[Callable] = None
        self._state_cb: Optional[Callable] = None
        self._error_cb: Optional[Callable] = None
        self._spectrum_cb: Optional[Callable] = None
    
    def on_position(self, cb): self._pos_cb = cb
    def on_state(self, cb): self._state_cb = cb
    def on_error(self, cb): self._error_cb = cb
    def on_spectrum(self, cb): self._spectrum_cb = cb
    
    def play_url(self, url: str):
        self.stop()
        self._current_url = url
        self._pos_s = 0.0
        self._stop_evt.clear()
        self._pause_evt.clear()
        threading.Thread(target=self._stream_and_play, args=(url, 0.0), daemon=True).start()
    
    def pause(self):
        with self._lock:
            if self._playing and not self._paused:
                self._pause_evt.set()
                self._paused = True
                self._playing = False
                self._kill_ffmpeg()
                self._emit_state(False)
    
    def resume(self):
        with self._lock:
            if not self._paused:
                return
            self._pause_evt.clear()
            self._paused = False
            self._playing = True
            pos = self._pos_s
            url = self._current_url
            threading.Thread(target=self._stream_and_play, args=(url, pos), daemon=True).start()
            self._emit_state(True)
    
    def stop(self):
        self._stop_evt.set()
        with self._lock:
            self._playing = False
            self._paused = False
            self._pos_s = 0.0
            self._kill_ffmpeg()
        self._stop_evt.clear()
        self._pause_evt.clear()
        self._emit_state(False)
    
    def seek(self, seconds: float):
        url = self._current_url
        if not url:
            return
        self._stop_evt.set()
        with self._lock:
            self._kill_ffmpeg()
            self._pos_s = seconds
            self._stop_evt.clear()
            self._pause_evt.clear()
        threading.Thread(target=self._stream_and_play, args=(url, seconds), daemon=True).start()
    
    def set_volume(self, pct: int):
        self._volume = max(0, min(100, pct))
    
    def is_playing(self) -> bool:
        return self._playing and not self._paused
    
    def poll(self):
        if self._playing or self._paused:
            self._emit_position(self._pos_s, self._dur_s)
    
    def _kill_ffmpeg(self):
        if self._ffmpeg_proc:
            try:
                self._ffmpeg_proc.kill()
                self._ffmpeg_proc.wait(timeout=2)
            except Exception:
                pass
        self._ffmpeg_proc = None
    
    def _stream_and_play(self, url: str, start_sec: float):
        bpf = self.CHANNELS * 2
        chunk_bytes = self.CHUNK_FRAMES * bpf
        cmd = [_ffmpeg_bin(), "-ss", str(start_sec), "-reconnect", "1", "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5", "-i", url, "-vn", "-acodec", "pcm_s16le",
            "-ar", str(self.SAMPLE_RATE), "-ac", str(self.CHANNELS), "-f", "s16le", "pipe:1"]
        try:
            _si = None
            _cflags = 0
            if hasattr(subprocess, "STARTUPINFO"):
                _si = subprocess.STARTUPINFO()
                _si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                _si.wShowWindow = 0
                _cflags = subprocess.CREATE_NO_WINDOW
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=chunk_bytes * 8, startupinfo=_si, creationflags=_cflags)
            with self._lock:
                self._ffmpeg_proc = proc
                self._playing = True
                self._paused = False
                self._emit_state(True)
            with sd.RawOutputStream(samplerate=self.SAMPLE_RATE, channels=self.CHANNELS, dtype="int16") as stream:
                while True:
                    if self._stop_evt.is_set() or self._pause_evt.is_set():
                        break
                    raw = proc.stdout.read(chunk_bytes)
                    if not raw:
                        break
                    arr = np.frombuffer(raw, dtype=np.int16).copy()
                    vol = self._volume / 100.0
                    if vol < 1.0:
                        arr = (arr * vol).astype(np.int16)
                    raw = arr.tobytes()
                    stream.write(raw)
                    self._pos_s += len(raw) // bpf / self.SAMPLE_RATE
                    if self._spectrum_cb:
                        try:
                            mono = arr.reshape(-1, 2).mean(axis=1).astype(np.float32)
                            n = len(mono)
                            if n >= 256:
                                window = np.hanning(n)
                                windowed = mono * window
                                fft_mag = np.abs(np.fft.rfft(windowed))
                                n_bands, n_bins = 40, len(fft_mag)
                                lo_bin = max(1, int(20 * n / self.SAMPLE_RATE))
                                hi_bin = min(n_bins - 1, int(16000 * n / self.SAMPLE_RATE))
                                edges = np.logspace(np.log10(lo_bin), np.log10(hi_bin), n_bands + 1).astype(int)
                                bands = []
                                for bi in range(n_bands):
                                    b0 = edges[bi]
                                    b1 = max(b0 + 1, edges[bi + 1])
                                    bands.append(float(np.mean(fft_mag[b0:b1])))
                                mx = max(bands) if max(bands) > 0 else 1.0
                                bands = [min(1.0, b / mx) for b in bands]
                                self._spectrum_cb(bands)
                        except Exception:
                            pass
        except Exception as exc:
            self._emit_error(str(exc))
        finally:
            with self._lock:
                self._kill_ffmpeg()
            if not self._paused:
                self._playing = False
            if not self._paused:
                self._emit_state(False)
    
    def _emit_position(self, p, d):
        if self._pos_cb: self._pos_cb(p, d)
    def _emit_state(self, s):
        if self._state_cb: self._state_cb(s)
    def _emit_error(self, m):
        if self._error_cb: self._error_cb(m)

# ==============================================================================
# ALBUM ART
# ==============================================================================
def _http_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Streamy/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()

def _theaudiodb_art(artist: str, album: str, title: str) -> Optional[str]:
    try:
        if artist and album:
            url = f"https://www.theaudiodb.com/api/v1/json/2/searchalbum.php?s={urllib.parse.quote(artist)}&a={urllib.parse.quote(album)}"
            req = urllib.request.Request(url, headers={"User-Agent": "Streamy/1.0"})
            data = json.loads(urllib.request.urlopen(req, timeout=8).read().decode())
            albums = data.get("album") or []
            for a in albums:
                for key in ("strAlbumThumb", "strAlbumThumbHQ", "strAlbumCDart"):
                    art = a.get(key)
                    if art:
                        return art
        if artist and title:
            url = f"https://www.theaudiodb.com/api/v1/json/2/searchtrack.php?s={urllib.parse.quote(artist)}&t={urllib.parse.quote(title)}"
            req = urllib.request.Request(url, headers={"User-Agent": "Streamy/1.0"})
            data = json.loads(urllib.request.urlopen(req, timeout=8).read().decode())
            tracks = data.get("track") or []
            for t in tracks:
                art = t.get("strTrackThumb") or t.get("strAlbumThumb")
                if art:
                    return art
    except Exception:
        pass
    return None

def fetch_album_art_async(artist: str, album: str, title: str, thumbnail_url: str, on_result: Callable) -> None:
    def _worker():
        art_url = _theaudiodb_art(artist, album, title)
        if art_url:
            try:
                raw = _http_bytes(art_url)
                img = Image.open(io.BytesIO(raw)).convert("RGBA")
                if img.size[0] >= 100:
                    on_result(img, "theaudiodb")
                    return
            except Exception:
                pass
        if thumbnail_url:
            try:
                raw = _http_bytes(thumbnail_url)
                img = Image.open(io.BytesIO(raw)).convert("RGBA")
                if img.size[0] >= 50:
                    on_result(img, "youtube_thumbnail")
                    return
            except Exception:
                pass
        if thumbnail_url:
            m = re.search(r"/vi(?:_webp)?/([^/]+)/", thumbnail_url)
            video_id = m.group(1) if m else ""
            if video_id:
                for res in ("maxresdefault", "sddefault", "hqdefault"):
                    try:
                        url = f"https://img.youtube.com/vi/{video_id}/{res}.jpg"
                        raw = _http_bytes(url)
                        img = Image.open(io.BytesIO(raw)).convert("RGBA")
                        if img.size[0] >= 50:
                            on_result(img, "youtube_thumbnail")
                            return
                    except Exception:
                        continue
        on_result(Image.new("RGBA", (300, 300), (30, 20, 50, 255)), "")
    threading.Thread(target=_worker, daemon=True).start()

# ==============================================================================
# LYRICS
# ==============================================================================
LyricLine = Tuple[float, str]

def _parse_lrc(lrc: str) -> List[LyricLine]:
    pattern = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")
    lines = []
    for match in pattern.finditer(lrc):
        mins = int(match.group(1))
        secs = float(match.group(2))
        text = match.group(3).strip()
        if text:
            lines.append((mins * 60 + secs, text))
    return sorted(lines, key=lambda x: x[0])

def fetch_lyrics_async(artist: str, title: str, on_plain: Callable, on_synced: Callable) -> None:
    def _worker():
        if artist and title:
            try:
                lrc_url = f"https://lrclib.net/api/get?artist_name={urllib.parse.quote(artist)}&track_name={urllib.parse.quote(title)}"
                req = urllib.request.Request(lrc_url, headers={"User-Agent": "Streamy/1.0"})
                data = json.loads(urllib.request.urlopen(req, timeout=8).read().decode())
                synced = data.get("syncedLyrics") or ""
                if synced:
                    parsed = _parse_lrc(synced)
                    if parsed:
                        on_synced(parsed)
                        return
                plain = (data.get("plainLyrics") or "").strip()
                if plain:
                    on_plain(plain)
                    return
            except Exception:
                pass
            try:
                url = f"https://api.lyrics.ovh/v1/{urllib.parse.quote(artist)}/{urllib.parse.quote(title)}"
                req = urllib.request.Request(url, headers={"User-Agent": "Streamy/1.0"})
                data = json.loads(urllib.request.urlopen(req, timeout=8).read().decode())
                plain = data.get("lyrics", "").strip()
                if plain:
                    on_plain(plain)
                    return
            except Exception:
                pass
        on_plain("Lyrics not found for this track.")
    threading.Thread(target=_worker, daemon=True).start()

# ==============================================================================
# QUEUE & HISTORY
# ==============================================================================
_HISTORY_FILE = os.path.join(os.path.expanduser("~"), ".metroiist_history.json")

class HistoryManager:
    def __init__(self):
        self._tracks: deque = deque(maxlen=50)
        self._load()
    def add(self, track: Track):
        self._tracks = deque((t for t in self._tracks if t.video_id != track.video_id), maxlen=50)
        self._tracks.appendleft(track)
        self._save()
    def all(self) -> List[Track]: return list(self._tracks)
    def clear(self): self._tracks.clear(); self._save()
    def _save(self):
        try:
            with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump([t.to_dict() for t in self._tracks], f, indent=2)
        except Exception: pass
    def _load(self):
        try:
            with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
                self._tracks = deque((Track.from_dict(d) for d in json.load(f)), maxlen=50)
        except Exception:
            self._tracks = deque(maxlen=50)

class QueueManager:
    def __init__(self):
        self._queue: List[Track] = []
        self._index: int = -1
    def set_tracks(self, tracks: List[Track], start: int = 0): self._queue = list(tracks); self._index = start
    def add(self, t: Track): self._queue.append(t)
    def remove(self, i: int):
        if 0 <= i < len(self._queue):
            if i < self._index: self._index -= 1
            elif i == self._index: self._index = max(0, self._index - 1)
            self._queue.pop(i)
    def next(self) -> Optional[Track]:
        if self._index + 1 < len(self._queue): self._index += 1; return self._queue[self._index]
        return None
    def previous(self) -> Optional[Track]:
        if self._index - 1 >= 0: self._index -= 1; return self._queue[self._index]
        return None
    def all(self) -> List[Track]: return list(self._queue)
    def current_index(self) -> int: return self._index
    def clear(self): self._queue.clear(); self._index = -1

def _fetch_related_async(video_id: str, on_results: Callable, artist: str = "") -> None:
    _fallback_artist = artist
    def _worker():
        tracks: List[Track] = []
        try:
            url = "https://music.youtube.com/youtubei/v1/next?alt=json&key=AIzaSyC9XL3ZjWddXya6X74dJoCTL-WEYFDNX30"
            payload = json.dumps({
                "context": {"client": {"clientName": "WEB_REMIX", "clientVersion": "1.20240101.01.00", "hl": "en", "gl": "US"}},
                "videoId": video_id, "isAudioOnly": True, "tunerSettingValue": "AUTOMIX_SETTING_NORMAL",
            }).encode()
            headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0",
                "X-YouTube-Client-Name": "67", "X-YouTube-Client-Version": "1.20240101.01.00"}
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=12) as r:
                data = json.loads(r.read().decode())
        except Exception:
            pass
        if tracks:
            on_results(tracks)
            return
        try:
            if not _fallback_artist: return
            fallback = _innertube_search(_fallback_artist)
            fallback = [t for t in fallback if t.video_id != video_id]
            if fallback:
                on_results(fallback[:20])
        except Exception:
            pass
        on_results([])
    threading.Thread(target=_worker, daemon=True).start()

# ==============================================================================
# HELPERS
# ==============================================================================
def _fmt(s: float) -> str:
    s = int(s)
    return f"{s // 60}:{s % 60:02d}"

def _placeholder_art(size: int = 300) -> Image.Image:
    img = Image.new("RGBA", (size, size), (20, 14, 40, 255))
    draw = ImageDraw.Draw(img)
    c, r = size // 2, size // 4
    draw.ellipse([c-r, c-r, c+r, c+r], fill=(80, 40, 160, 120))
    draw.ellipse([c-r//2, c-r//2, c+r//2, c+r//2], fill=(140, 84, 247, 180))
    return img

def _rounded_photo(img: Image.Image, size: int, mode: str = "fit") -> ctk.CTkImage:
    img = img.convert("RGBA")
    iw, ih = img.size
    if mode == "fill":
        scale = max(size / iw, size / ih)
        nw, nh = int(iw * scale), int(ih * scale)
        img = img.resize((nw, nh), Image.LANCZOS)
        img = img.crop(((nw - size) // 2, (nh - size) // 2, (nw + size) // 2, (nh + size) // 2))
    else:
        scale = min(size / iw, size / ih)
        nw, nh = int(iw * scale), int(ih * scale)
        img = img.resize((nw, nh), Image.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.paste(img, ((size - nw) // 2, (size - nh) // 2))
        img = canvas
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size-1, size-1], radius=size//10, fill=255)
    img.putalpha(mask)
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))

# ==============================================================================
# 3-DOT MENU POPUP
# ==============================================================================
class ThreeDotMenu(tk.Toplevel):
    def __init__(self, parent, track, on_add_to_playlist, on_add_to_queue, on_like, on_download, on_visit_artist, on_visit_album):
        super().__init__(parent)
        self.title("")
        self.overrideredirect(True)
        self.configure(bg="#1c1c1e")
        self.resizable(False, False)
        
        # Get parent position
        x = parent.winfo_rootx() + parent.winfo_width() - 180
        y = parent.winfo_rooty() + parent.winfo_height() // 2 - 100
        self.geometry(f"180x220+{x}+{y}")
        
        # Make it stay on top
        self.attributes("-topmost", True)
        
        t = DARK
        
        # Title
        title_lbl = ctk.CTkLabel(self, text=track.title[:25] + "..." if len(track.title) > 25 else track.title,
            font=_font(11, "bold"), text_color=t["text"], bg_color="#1c1c1e", anchor="w")
        title_lbl.pack(fill="x", padx=12, pady=(12, 8))
        
        # Menu items
        menu_items = [
            ("❤ Like", on_like),
            ("➕ Add to Queue", on_add_to_queue),
            ("📁 Add to Playlist", on_add_to_playlist),
            ("⬇ Download", on_download),
            ("👤 Visit Artist", on_visit_artist),
            ("💿 Visit Album", on_visit_album),
        ]
        
        for text, command in menu_items:
            if command:
                btn = ctk.CTkButton(self, text=text, height=32, anchor="w",
                    font=_font(11), fg_color="transparent",
                    text_color=t["text_secondary"], hover_color=t["bg_hover"],
                    command=command)
                btn.pack(fill="x", padx=4, pady=1)
        
        # Close on click outside
        self.bind("<Button-1>", lambda e: self.destroy())
        self.focus_set()
        
        # Auto-close after 10 seconds
        self.after(10000, self.destroy)

# ==============================================================================
# MAIN APP
# ==============================================================================
class StreamyApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self._theme = DARK
        self._search_results: List[Track] = []
        self._current_track: Optional[Track] = None
        self._duration = 0.0
        self._seeking = False
        self._art_photo = None
        self._banner_photo = None
        self._art_image: Optional[Image.Image] = None
        self._result_thumb_refs: list = []
        self._synced_lines: List[LyricLine] = []
        self._lyric_labels: List[ctk.CTkLabel] = []
        self._active_lyric = -1
        self._audio = AudioPlayer()
        self._queue = QueueManager()
        self._history = HistoryManager()
        self._playlists = PlaylistManager()
        self._downloads = DownloadManager()
        
        # Wire up callbacks
        self._audio.on_position(lambda p, d: self.after(0, lambda: self._update_pos(p, d)))
        self._audio.on_state(lambda s: self.after(0, lambda: self._on_state(s)))
        self._audio.on_error(lambda e: self.after(0, lambda: self._status("Playback error: " + e)))
        # FIX: Wire up spectrum callback for real visualizer
        self._audio.on_spectrum(lambda b: self.after(0, lambda: self._on_spectrum(b)))
        
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        self.title("Streamy")
        self.geometry("1280x820")
        self.minsize(960, 680)
        self._build_ui()
        self._apply_theme()
        self._set_art(_placeholder_art())
        self._poll()
    
    def _build_ui(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._build_main()
        self._build_player_bar()
        self._build_status_bar()
    
    def _build_main(self):
        self._main = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self._main.grid(row=0, column=0, sticky="nsew")
        self._main.grid_columnconfigure(1, weight=1)
        self._main.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_content()
    
    def _build_sidebar(self):
        t = self._theme
        self._sb = ctk.CTkFrame(self._main, width=220, corner_radius=0, fg_color=t["sidebar_bg"])
        self._sb.grid(row=0, column=0, sticky="ns")
        self._sb.grid_propagate(False)
        self._sb.grid_rowconfigure(4, weight=1)
        logo_frame = ctk.CTkFrame(self._sb, fg_color="transparent", height=70)
        logo_frame.pack(fill="x", padx=0)
        logo_frame.pack_propagate(False)
        self._logo = ctk.CTkLabel(logo_frame, text="🎵 Streamy", font=_font(22, "bold"),
            text_color=t["text"], anchor="w")
        self._logo.pack(padx=20, pady=18, anchor="w")
        sf = ctk.CTkFrame(self._sb, fg_color="transparent")
        sf.pack(fill="x", padx=12, pady=(0, 8))
        sf.grid_columnconfigure(0, weight=1)
        self._search_var = tk.StringVar()
        self._search_entry = ctk.CTkEntry(sf, textvariable=self._search_var,
            placeholder_text="🔍 Search...", height=38, corner_radius=19, font=_font(13),
            fg_color=t["bg_secondary"], border_color=t["bg_hover"], border_width=1,
            text_color=t["text"])
        self._search_entry.grid(row=0, column=0, sticky="ew")
        self._search_entry.bind("<Return>", lambda _: self._do_search())
        self._search_btn = ctk.CTkButton(sf, text="Go", width=46, height=38, corner_radius=19,
            command=self._do_search, font=_font(12, "bold"), fg_color=t["btn_primary"],
            text_color=t["btn_text"], hover_color=t["accent"])
        self._search_btn.grid(row=0, column=1, padx=(6, 0))
        self._loading_lbl = ctk.CTkLabel(sf, text="⏳", font=_font(14))
        ctk.CTkFrame(self._sb, height=1, fg_color=t["bg_hover"]).pack(fill="x", padx=16, pady=4)
        nav_items = [
            ("🔍 Search", "results"),
            ("🎵 Now Playing", "nowplaying"),
            ("📁 Playlists", "playlists"),
            ("⬇ Downloads", "downloads"),
            ("📋 Queue", "queue"),
            ("🕒 History", "history"),
            ("📄 Lyrics", "lyrics"),
            ("⚙️ Settings", "settings"),
        ]
        self._nav_btns = {}
        nav_frame = ctk.CTkFrame(self._sb, fg_color="transparent")
        nav_frame.pack(fill="x", padx=8, pady=4)
        for label, key in nav_items:
            btn = ctk.CTkButton(nav_frame, text=label, height=38, anchor="w", corner_radius=8,
                font=_font(12), fg_color="transparent", text_color=t["text_secondary"],
                hover_color=t["bg_hover"], command=lambda k=key: self._switch_tab(k))
            btn.pack(fill="x", padx=4, pady=1)
            self._nav_btns[key] = btn
        ctk.CTkFrame(self._sb, height=1, fg_color=t["bg_hover"]).pack(fill="x", padx=16, pady=8)
        ctk.CTkLabel(self._sb, text="RECENTLY PLAYED", font=_font(10),
            text_color=t["text_muted"], anchor="w").pack(fill="x", padx=20, pady=(0, 6))
        self._history_sidebar = ctk.CTkScrollableFrame(self._sb, fg_color="transparent",
            scrollbar_button_color=t["bg_hover"])
        self._history_sidebar.pack(fill="both", expand=True, padx=8)
        ctk.CTkFrame(self._sb, height=1, fg_color=t["bg_hover"]).pack(fill="x", padx=16, pady=(4, 0))
        np_row = ctk.CTkFrame(self._sb, fg_color="transparent", height=60)
        np_row.pack(fill="x", padx=12, pady=8)
        np_row.pack_propagate(False)
        np_row.grid_columnconfigure(1, weight=1)
        self._art_lbl = ctk.CTkLabel(np_row, text="", width=44, height=44)
        self._art_lbl.grid(row=0, column=0, rowspan=2, padx=(0, 10))
        self._np_title = ctk.CTkLabel(np_row, text="Not playing", font=_font(12, "bold"),
            text_color=t["text"], anchor="w", wraplength=140)
        self._np_title.grid(row=0, column=1, sticky="sw")
        self._np_artist = ctk.CTkLabel(np_row, text="", font=_font(10),
            text_color=t["text_muted"], anchor="w")
        self._np_artist.grid(row=1, column=1, sticky="nw")
        self._np_album = ctk.CTkLabel(np_row, text="")
        self._art_source = ctk.CTkLabel(np_row, text="")
    
    def _build_content(self):
        self._content = ctk.CTkFrame(self._main, corner_radius=0, fg_color=self._theme["bg"])
        self._content.grid(row=0, column=1, sticky="nsew")
        self._content.grid_rowconfigure(0, weight=1)
        self._content.grid_columnconfigure(0, weight=1)
        self._active_tab = tk.StringVar(value="results")
        self._tab_content = ctk.CTkFrame(self._content, corner_radius=0, fg_color="transparent")
        self._tab_content.grid(row=0, column=0, sticky="nsew")
        self._tab_content.grid_rowconfigure(0, weight=1)
        self._tab_content.grid_columnconfigure(0, weight=1)
        self._build_results_tab()
        self._build_nowplaying_tab()
        self._build_playlists_tab()
        self._build_downloads_tab()
        self._build_queue_tab()
        self._build_history_tab()
        self._build_lyrics_tab()
        self._build_settings_tab()
        self._switch_tab("results")
    
    def _build_results_tab(self):
        t = self._theme
        self._results_frame = ctk.CTkFrame(self._tab_content, corner_radius=0, fg_color="transparent")
        self._results_frame.grid_rowconfigure(1, weight=1)
        self._results_frame.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(self._results_frame, fg_color="transparent", height=56)
        top.grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 0))
        top.grid_propagate(False)
        self._page_title = ctk.CTkLabel(top, text="Good morning ☀", font=_font(28, "bold"),
            text_color=t["text"], anchor="w")
        self._page_title.grid(row=0, column=0, sticky="w")
        self._results_list = ctk.CTkScrollableFrame(self._results_frame, corner_radius=0,
            fg_color="transparent", scrollbar_button_color=t["bg_hover"])
        self._results_list.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
        self._results_hint = ctk.CTkLabel(self._results_list, text="Search for a song, artist, or album 🔍",
            font=_font(16), text_color=t["text_muted"])
        self._results_hint.pack(expand=True, pady=80)
        self._result_rows: list = []
    
    def _build_nowplaying_tab(self):
        t = self._theme
        self._np_frame = ctk.CTkFrame(self._tab_content, corner_radius=0, fg_color="transparent")
        self._np_frame.grid_columnconfigure(0, weight=5)
        self._np_frame.grid_columnconfigure(1, weight=3)
        self._np_frame.grid_rowconfigure(0, weight=1)
        left = ctk.CTkFrame(self._np_frame, corner_radius=0, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(28, 12), pady=24)
        ART_SIZE = 420
        self._np_big_art = ctk.CTkLabel(left, text="", width=ART_SIZE, height=ART_SIZE,
            fg_color=t["bg_secondary"], corner_radius=20)
        self._np_big_art.grid(row=0, column=0, sticky="nw", padx=(0, 32))
        info = ctk.CTkFrame(left, fg_color="transparent")
        info.grid(row=0, column=1, sticky="nw", pady=8)
        info.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(info, text="N O W   P L A Y I N G", font=_font(10),
            text_color=t["text_muted"], anchor="w").grid(row=0, column=0, sticky="w", pady=(0, 20))
        self._np_big_title = ctk.CTkLabel(info, text="Nothing playing", font=_font(28, "bold"),
            wraplength=340, justify="left", text_color=t["text"], anchor="w")
        self._np_big_title.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self._np_big_artist = ctk.CTkLabel(info, text="", font=_font(17), wraplength=340,
            justify="left", text_color=t["text_secondary"], anchor="w")
        self._np_big_artist.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        self._np_big_album = ctk.CTkLabel(info, text="", font=_font(14), wraplength=340,
            justify="left", text_color=t["text_muted"], anchor="w")
        self._np_big_album.grid(row=3, column=0, sticky="ew")
        self._play_count_lbl = ctk.CTkLabel(info, text="", font=_font(11),
            text_color=t["accent_light"], anchor="w")
        self._play_count_lbl.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        viz_outer = ctk.CTkFrame(left, corner_radius=16, fg_color=t["bg_secondary"], height=300)
        viz_outer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(20, 0))
        viz_outer.grid_propagate(False)
        viz_outer.grid_columnconfigure(0, weight=1)
        self._viz_canvas = tk.Canvas(viz_outer, bg=t["bg_secondary"], highlightthickness=0, bd=0)
        self._viz_canvas.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self._viz_bars = 48
        self._viz_heights = [0.0] * 48
        self._viz_targets = [0.0] * 48
        self._viz_spectrum = [0.0] * 48
        self._viz_color = "#c084fc"
        self._viz_pos_s = 0.0
        self._animate_viz()
        right = ctk.CTkFrame(self._np_frame, corner_radius=20, fg_color=t["bg_secondary"])
        right.grid(row=0, column=1, sticky="nsew", padx=(0, 24), pady=24)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(right, text="Lyrics", font=_font(18, "bold"), text_color=t["text"]
            ).grid(row=0, column=0, pady=(20, 10), padx=20, sticky="w")
        self._synced_scroll = ctk.CTkScrollableFrame(right, corner_radius=0, fg_color="transparent")
        self._synced_scroll.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 8))
        self._np_lyrics_plain = ctk.CTkLabel(self._synced_scroll, text="Play a song to see lyrics",
            font=_font(14), text_color=t["text_muted"], wraplength=340, justify="left")
        self._np_lyrics_plain.pack(padx=16, pady=16, anchor="w")
    
    def _build_playlists_tab(self):
        t = self._theme
        self._playlists_frame = ctk.CTkFrame(self._tab_content, corner_radius=0, fg_color="transparent")
        self._playlists_frame.grid_rowconfigure(1, weight=1)
        self._playlists_frame.grid_columnconfigure(0, weight=1)
        hdr = ctk.CTkFrame(self._playlists_frame, fg_color="transparent", height=56)
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 0))
        hdr.grid_propagate(False)
        ctk.CTkLabel(hdr, text="Playlists", font=_font(28, "bold"), text_color=t["text"], anchor="w"
            ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(hdr, text="+ New", width=72, height=32, corner_radius=16,
            command=self._create_playlist_dialog, font=_font(12), fg_color=t["btn_primary"],
            text_color=t["btn_text"]).grid(row=0, column=1, sticky="e")
        self._playlists_list = ctk.CTkScrollableFrame(self._playlists_frame, corner_radius=0, fg_color="transparent")
        self._playlists_list.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
        self._playlist_tracks_frame = ctk.CTkFrame(self._playlists_frame, corner_radius=0, fg_color="transparent")
        self._playlist_tracks_frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
        self._playlist_tracks_frame.grid_remove()
        self._current_playlist = None
    
    def _build_downloads_tab(self):
        t = self._theme
        self._downloads_frame = ctk.CTkFrame(self._tab_content, corner_radius=0, fg_color="transparent")
        self._downloads_frame.grid_rowconfigure(1, weight=1)
        self._downloads_frame.grid_columnconfigure(0, weight=1)
        hdr = ctk.CTkFrame(self._downloads_frame, fg_color="transparent", height=56)
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 0))
        hdr.grid_propagate(False)
        ctk.CTkLabel(hdr, text="Downloads", font=_font(28, "bold"), text_color=t["text"], anchor="w"
            ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(hdr, text=f"Stored in: {_DOWNLOADS_DIR}", font=_font(10),
            text_color=t["text_muted"], anchor="w").grid(row=1, column=0, sticky="w", padx=24)
        self._downloads_list = ctk.CTkScrollableFrame(self._downloads_frame, corner_radius=0, fg_color="transparent")
        self._downloads_list.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
    
    def _build_queue_tab(self):
        t = self._theme
        self._queue_frame = ctk.CTkFrame(self._tab_content, corner_radius=0, fg_color="transparent")
        self._queue_frame.grid_rowconfigure(1, weight=1)
        self._queue_frame.grid_columnconfigure(0, weight=1)
        hdr = ctk.CTkFrame(self._queue_frame, fg_color="transparent", height=56)
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 0))
        hdr.grid_propagate(False)
        ctk.CTkLabel(hdr, text="Queue", font=_font(28, "bold"), text_color=t["text"], anchor="w"
            ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(hdr, text="Clear", width=72, height=32, corner_radius=16,
            command=self._clear_queue, font=_font(12), fg_color=t["btn_secondary"],
            text_color=t["btn_secondary_text"]).grid(row=0, column=1, sticky="e")
        self._queue_list = ctk.CTkScrollableFrame(self._queue_frame, corner_radius=0, fg_color="transparent")
        self._queue_list.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
    
    def _build_history_tab(self):
        t = self._theme
        self._history_frame = ctk.CTkFrame(self._tab_content, corner_radius=0, fg_color="transparent")
        self._history_frame.grid_rowconfigure(1, weight=1)
        self._history_frame.grid_columnconfigure(0, weight=1)
        hdr = ctk.CTkFrame(self._history_frame, fg_color="transparent", height=56)
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 0))
        hdr.grid_propagate(False)
        ctk.CTkLabel(hdr, text="Recently Played", font=_font(28, "bold"), text_color=t["text"], anchor="w"
            ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(hdr, text="Clear", width=72, height=32, corner_radius=16,
            command=self._clear_history, font=_font(12), fg_color=t["btn_secondary"],
            text_color=t["btn_secondary_text"]).grid(row=0, column=1, sticky="e")
        self._history_list = ctk.CTkScrollableFrame(self._history_frame, corner_radius=0, fg_color="transparent")
        self._history_list.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
    
    def _build_lyrics_tab(self):
        t = self._theme
        self._lyrics_frame = ctk.CTkFrame(self._tab_content, corner_radius=0, fg_color="transparent")
        self._lyrics_frame.grid_rowconfigure(1, weight=1)
        self._lyrics_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self._lyrics_frame, text="Lyrics", font=_font(28, "bold"),
            text_color=t["text"], anchor="w").grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 8))
        self._lyrics_text = ctk.CTkTextbox(self._lyrics_frame, corner_radius=12, font=_font(14),
            wrap="word", state="disabled", fg_color=t["bg_secondary"], text_color=t["text"])
        self._lyrics_text.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
    
    def _build_player_bar(self):
        t = self._theme
        self._pb = ctk.CTkFrame(self, corner_radius=0, height=90, fg_color=t["player_bg"])
        self._pb.grid(row=1, column=0, sticky="ew")
        self._pb.grid_propagate(False)
        self._pb.grid_columnconfigure(1, weight=1)
        left = ctk.CTkFrame(self._pb, fg_color="transparent", width=280)
        left.grid(row=0, column=0, sticky="w", padx=(16, 0), pady=8)
        left.grid_propagate(False)
        left.grid_columnconfigure(1, weight=1)
        self._pb_art = ctk.CTkLabel(left, text="", width=56, height=56,
            fg_color=t["bg_secondary"], corner_radius=6)
        self._pb_art.grid(row=0, column=0, rowspan=2, padx=(0, 12))
        self._pb_title = ctk.CTkLabel(left, text="Nothing playing", font=_font(13, "bold"),
            text_color=t["text"], anchor="w", wraplength=200)
        self._pb_title.grid(row=0, column=1, sticky="sw", pady=(10, 1))
        self._pb_artist = ctk.CTkLabel(left, text="", font=_font(11),
            text_color=t["text_muted"], anchor="w", wraplength=200)
        self._pb_artist.grid(row=1, column=1, sticky="nw", pady=(1, 0))
        centre = ctk.CTkFrame(self._pb, fg_color="transparent")
        centre.grid(row=0, column=1, sticky="nsew", pady=4)
        centre.grid_columnconfigure(0, weight=1)
        ctrl = ctk.CTkFrame(centre, fg_color="transparent")
        ctrl.grid(row=0, column=0, pady=(6, 2))
        self._prev_btn = ctk.CTkButton(ctrl, text="⏮", width=36, height=36, corner_radius=18,
            command=self._prev, font=_font(16), fg_color="transparent",
            text_color=t["text_secondary"], hover_color=t["bg_hover"])
        self._prev_btn.pack(side="left", padx=4)
        self._play_btn = ctk.CTkButton(ctrl, text="▶", width=48, height=48, corner_radius=24,
            command=self._toggle_play, font=_font(18, "bold"), fg_color=t["btn_primary"],
            text_color=t["btn_text"], hover_color=t["accent"])
        self._play_btn.pack(side="left", padx=6)
        self._next_btn = ctk.CTkButton(ctrl, text="⏭", width=36, height=36, corner_radius=18,
            command=self._next, font=_font(16), fg_color="transparent",
            text_color=t["text_secondary"], hover_color=t["bg_hover"])
        self._next_btn.pack(side="left", padx=4)
        seek_row = ctk.CTkFrame(centre, fg_color="transparent")
        seek_row.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 4))
        seek_row.grid_columnconfigure(1, weight=1)
        self._pos_lbl = ctk.CTkLabel(seek_row, text="0:00", width=38, font=_font(11),
            text_color=t["text_muted"])
        self._pos_lbl.grid(row=0, column=0, padx=(0, 6))
        self._seek_var = tk.DoubleVar(value=0)
        self._seek_bar = ctk.CTkSlider(seek_row, from_=0, to=1000, variable=self._seek_var,
            command=self._on_seek_drag, height=14, button_color=t["btn_primary"],
            progress_color=t["btn_primary"], fg_color=t["bg_hover"])
        self._seek_bar.grid(row=0, column=1, sticky="ew")
        self._seek_bar.bind("<ButtonRelease-1>", self._on_seek_release)
        self._dur_lbl = ctk.CTkLabel(seek_row, text="0:00", width=38, font=_font(11),
            text_color=t["text_muted"])
        self._dur_lbl.grid(row=0, column=2, padx=(6, 0))
        right = ctk.CTkFrame(self._pb, fg_color="transparent", width=180)
        right.grid(row=0, column=2, sticky="e", padx=(0, 20), pady=8)
        right.grid_propagate(False)
        ctk.CTkLabel(right, text="🔊", font=_font(14), text_color=t["text_muted"]).pack(side="left", padx=(8, 6))
        self._vol_var = tk.DoubleVar(value=80)
        ctk.CTkSlider(right, from_=0, to=100, variable=self._vol_var,
            command=lambda v: self._audio.set_volume(int(v)), width=100, height=12,
            button_color=t["btn_primary"], progress_color=t["btn_primary"],
            fg_color=t["bg_hover"]).pack(side="left")
    
    def _build_status_bar(self):
        t = self._theme
        self._sbar = ctk.CTkFrame(self, corner_radius=0, height=22, fg_color=t["status_bg"])
        self._sbar.grid(row=2, column=0, sticky="ew")
        self._sbar.grid_propagate(False)
        self._status_lbl = ctk.CTkLabel(self._sbar, text="Ready", font=_font(10),
            anchor="w", text_color=t["status_text"])
        self._status_lbl.pack(side="left", padx=12)
    
    def _switch_tab(self, key: str):
        self._active_tab.set(key)
        panels = {
            "results": self._results_frame,
            "nowplaying": self._np_frame,
            "playlists": self._playlists_frame,
            "downloads": self._downloads_frame,
            "queue": self._queue_frame,
            "history": self._history_frame,
            "lyrics": self._lyrics_frame,
            "settings": self._settings_frame,
        }
        for k, p in panels.items():
            if k == key: p.grid(row=0, column=0, sticky="nsew")
            else: p.grid_remove()
        t = self._theme
        for k, btn in self._nav_btns.items():
            if k == key:
                btn.configure(fg_color=t.get("sidebar_item_active", "#1e1e2e"),
                    text_color=t.get("accent_light", "#c084fc"), font=_font(12, "bold"))
            else:
                btn.configure(fg_color="transparent", text_color=t.get("text_secondary", "#b3b3cc"), font=_font(12))
        if key == "history":
            self._refresh_history()
            self._refresh_history_sidebar()
        elif key == "queue":
            self._refresh_queue()
        elif key == "playlists":
            self._refresh_playlists()
        elif key == "downloads":
            self._refresh_downloads()
        elif key == "lyrics" and self._current_track:
            self._load_lyrics(self._current_track)
    
    def _do_search(self):
        query = self._search_var.get().strip()
        if not query: return
        self._status("Searching: " + query)
        self._search_btn.configure(state="disabled")
        self._loading_lbl.grid(row=0, column=2, padx=(4, 0))
        search_async(query, self._on_search_results, self._on_search_error)
    
    def _on_search_results(self, tracks):
        self.after(0, lambda: self._display_results(tracks))
    
    def _on_search_error(self, msg):
        self.after(0, lambda: (self._status("Error: " + msg), self._search_btn.configure(state="normal"), self._loading_lbl.grid_remove()))
    
    def _display_results(self, tracks):
        self._search_results = tracks
        self._search_btn.configure(state="normal")
        self._loading_lbl.grid_remove()
        self._status(f"Found {len(tracks)} results")
        self._switch_tab("results")
        for w in self._result_rows: w.destroy()
        self._result_rows.clear()
        self._result_thumb_refs = []
        self._results_hint.pack_forget()
        if not tracks:
            self._results_hint.pack(expand=True, pady=60)
            return
        t = self._theme
        for i, track in enumerate(tracks):
            row = ctk.CTkFrame(self._results_list, corner_radius=16, height=72, fg_color="#1c1c1e")
            row.pack(fill="x", padx=12, pady=3)
            row.pack_propagate(False)
            row.grid_columnconfigure(2, weight=1)
            art_lbl = ctk.CTkLabel(row, text="", width=52, height=52, fg_color="#2c2c2e", corner_radius=8)
            art_lbl.grid(row=0, column=0, rowspan=2, padx=(10, 8), pady=10)
            if track.thumbnail_url:
                def _load(url=track.thumbnail_url, lbl=art_lbl):
                    def _fetch():
                        try:
                            img = Image.open(io.BytesIO(_http_bytes(url))).convert("RGBA")
                            photo = _rounded_photo(img, 52)
                            self._result_thumb_refs.append(photo)
                            self.after(0, lambda: lbl.configure(image=photo, text=""))
                        except: pass
                    threading.Thread(target=_fetch, daemon=True).start()
                _load()
            ctk.CTkLabel(row, text=f"{i+1}.", width=24, font=_font(11), text_color=t["text_muted"]
                ).grid(row=0, column=1, padx=(0, 4), pady=(12, 0), sticky="sw")
            ctk.CTkLabel(row, text=track.title or "Unknown", font=_font(13, "bold"),
                anchor="w", text_color="#ffffff").grid(row=0, column=2, sticky="ew", padx=4, pady=(14, 0))
            sub = track.artist
            if track.album: sub += " · " + track.album
            if track.duration: sub += " · " + track.duration
            ctk.CTkLabel(row, text=sub, font=_font(11), text_color="#8e8e93", anchor="w"
                ).grid(row=1, column=2, sticky="ew", padx=4, pady=(0, 12))
            bf = ctk.CTkFrame(row, fg_color="transparent")
            bf.grid(row=0, column=3, rowspan=2, padx=(4, 10))
            ctk.CTkButton(bf, text="▶", width=36, height=36, corner_radius=18, font=_font(14),
                fg_color="#ffffff", text_color="#000000", hover_color="#e0e0e0",
                command=lambda tr=track, idx=i: self._play_track(tr, idx)).pack(side="left", padx=3)
            # 3-dot menu button
            menu_btn = ctk.CTkButton(bf, text="⋮", width=36, height=36, corner_radius=18, font=_font(16),
                fg_color=t["btn_secondary"], text_color=t["btn_secondary_text"], hover_color=t["bg_hover"],
                command=lambda tr=track, r=row: self._show_track_menu(r, tr))
            menu_btn.pack(side="left", padx=3)
            self._result_rows.append(row)
    
    def _show_track_menu(self, parent, track):
        # Close any existing menu
        for w in self.winfo_children():
            if isinstance(w, ThreeDotMenu):
                w.destroy()
        
        def on_add_to_playlist():
            self._show_playlist_selector(track)
        def on_add_to_queue():
            self._queue_track(track)
        def on_like():
            self._like_track(track)
        def on_download():
            self._download_track(track)
        def on_visit_artist():
            self._search_var.set(track.artist)
            self._do_search()
        def on_visit_album():
            if track.album:
                self._search_var.set(f"{track.artist} {track.album}")
                self._do_search()
        
        ThreeDotMenu(parent, track, on_add_to_playlist, on_add_to_queue, on_like, on_download, on_visit_artist, on_visit_album)
    
    def _show_playlist_selector(self, track):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Add to Playlist")
        dialog.geometry("300x400")
        dialog.transient(self)
        dialog.grab_set()
        ctk.CTkLabel(dialog, text="Select Playlist", font=_font(14, "bold")).pack(pady=(20, 10))
        scroll = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=20, pady=10)
        for name in self._playlists.get_all_playlists():
            in_playlist = self._playlists.is_in_playlist(name, track.video_id)
            btn = ctk.CTkButton(scroll, text=f"{'✓ ' if in_playlist else ''}{name}", height=36,
                font=_font(12), fg_color=t["btn_secondary"] if in_playlist else None,
                command=lambda n=name: (self._playlists.add_to_playlist(n, track), self._status(f"Added to {n}"), dialog.destroy()))
            btn.pack(fill="x", pady=2)
        ctk.CTkButton(dialog, text="Cancel", height=32, font=_font(12),
            command=dialog.destroy).pack(pady=(0, 20))
    
    def _like_track(self, track):
        if self._playlists.add_to_playlist("Liked", track):
            self._status(f"Added to Liked: {track.title}")
        else:
            self._status("Already in Liked playlist")
    
    def _download_track(self, track):
        if self._downloads.is_downloaded(track.video_id):
            self._status("Already downloaded")
            return
        def _download():
            try:
                ydl_opts = {
                    "format": "bestaudio/best",
                    "outtmpl": os.path.join(_DOWNLOADS_DIR, "%(title)s.%(ext)s"),
                    "quiet": True,
                    "no_warnings": True,
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                }
                with YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(f"https://www.youtube.com/watch?v={track.video_id}", download=True)
                    filename = ydl.prepare_filename(info)
                    filename = os.path.splitext(filename)[0] + ".mp3"
                    if os.path.exists(filename):
                        self._downloads.add_download(track, filename)
                        self.after(0, lambda: self._status(f"Downloaded: {track.title}"))
            except Exception as e:
                self.after(0, lambda: self._status(f"Download failed: {str(e)}"))
        threading.Thread(target=_download, daemon=True).start()
        self._status("Downloading...")
    
    def _play_track(self, track, queue_index=-1):
        if queue_index >= 0:
            self._queue.set_tracks(self._search_results, queue_index)
        self._current_track = track
        self._synced_lines = []
        self._active_lyric = -1
        self._status("Loading: " + track.title)
        self._play_btn.configure(text="⏳", state="disabled")
        self._np_title.configure(text=track.title or "Unknown")
        self._np_artist.configure(text=track.artist or "")
        self._pb_title.configure(text=track.title or "Unknown")
        self._pb_artist.configure(text=track.artist or "")
        self._np_big_title.configure(text=track.title or "Unknown")
        self._np_big_artist.configure(text=track.artist or "--")
        self._np_lyrics_plain.configure(text="Loading lyrics...")
        for lbl in self._lyric_labels: lbl.destroy()
        self._lyric_labels.clear()
        self._set_art(_placeholder_art())
        self._history.add(track)
        self.after(0, self._refresh_history_sidebar)
        # Track play count and auto-like after 5 plays
        play_count = self._playlists.increment_play_count(track.video_id)
        if play_count == 5:
            self._playlists.add_to_playlist("Liked", track)
            self._status(f"Auto-added to Liked (played {play_count} times)")
        self._play_count_lbl.configure(text=f"▶ Played {play_count} times")
        self._queue.clear()
        def _on_related(tracks):
            for rt in tracks: self._queue.add(rt)
            self.after(0, lambda: self._status(f"Now playing: {track.display_name()}"))
        _fetch_related_async(track.video_id, _on_related, track.artist)
        fetch_album_art_async(track.artist, track.album, track.title, track.thumbnail_url,
            lambda img, src: self.after(0, lambda: self._on_art(img, src)))
        fetch_lyrics_async(track.artist, track.title,
            lambda text: self.after(0, lambda: self._show_plain_lyrics(text)),
            lambda lines: self.after(0, lambda: self._show_synced_lyrics(lines)))
        resolve_stream_async(track, self._on_resolved, self._on_resolve_err)
    
    def _on_resolved(self, track, url, thumb_url):
        self.after(0, lambda: self._start_playback(track, url, thumb_url))
    
    def _start_playback(self, track, url, thumb_url):
        self._status("Now playing: " + track.display_name())
        self._play_btn.configure(text="⏸", state="normal")
        self._audio.play_url(url)
        if thumb_url and thumb_url != track.thumbnail_url:
            fetch_album_art_async(track.artist, track.album, track.title, thumb_url,
                lambda img, src: self.after(0, lambda: self._on_art(img, src)))
    
    def _on_resolve_err(self, vid, err):
        self.after(0, lambda: (self._status("Error: " + err), self._play_btn.configure(text="▶", state="normal")))
    
    def _on_art(self, img, source):
        self._art_image = img
        self._set_art(img)
        try:
            new_palette = extract_palette(img)
            self._theme = new_palette
            self._apply_theme()
            self._update_viz_color(new_palette.get("btn_primary", "#ffffff"))
        except: pass
    
    def _set_art(self, img):
        self._art_image = img
        self._art_photo = _rounded_photo(img, 44)
        self._art_lbl.configure(image=self._art_photo, text="")
        self._pb_art_photo = _rounded_photo(img, 56)
        self._pb_art.configure(image=self._pb_art_photo, text="")
        self._np_big_art_photo = _rounded_photo(img, 420, mode="fill")
        self._np_big_art.configure(image=self._np_big_art_photo, text="")
    
    def _toggle_play(self):
        if self._audio.is_playing():
            self._audio.pause()
            self._play_btn.configure(text="▶")
        else:
            self._audio.resume()
            self._play_btn.configure(text="⏸")
    
    def _next(self):
        track = self._queue.next()
        if track: self._play_track(track)
        else: self._status("End of queue")
    
    def _prev(self):
        track = self._queue.previous()
        if track: self._play_track(track)
    
    def _on_seek_drag(self, value):
        self._seeking = True
        self._pos_lbl.configure(text=_fmt(float(value) / 1000 * self._duration))
    
    def _on_seek_release(self, _):
        if self._duration > 0:
            self._audio.seek(self._seek_var.get() / 1000 * self._duration)
        self._seeking = False
    
    def _on_state(self, playing):
        self._play_btn.configure(text="⏸" if playing else "▶")
    
    def _update_pos(self, pos, dur):
        if dur > 0: self._duration = dur
        if not self._seeking and self._duration > 0:
            self._seek_var.set(pos / self._duration * 1000)
            self._pos_lbl.configure(text=_fmt(pos))
            self._dur_lbl.configure(text=_fmt(self._duration))
            self._update_viz_pos(pos)
        if self._synced_lines: self._highlight_lyric(pos)
    
    def _poll(self):
        self._audio.poll()
        self.after(500, self._poll)
    
    # ==================== VISUALIZER ====================
    def _on_spectrum(self, bands):
        """Receive real FFT spectrum data from AudioPlayer."""
        for i, val in enumerate(bands):
            if i < len(self._viz_spectrum):
                self._viz_spectrum[i] = self._viz_spectrum[i] * 0.3 + val * 0.7
        for i in range(min(len(bands), self._viz_bars)):
            self._viz_targets[i] = self._viz_spectrum[i]
    
    def _animate_viz(self):
        import math, random
        try:
            canvas = self._viz_canvas
            cw, ch = canvas.winfo_width(), canvas.winfo_height()
            if cw < 10 or ch < 10:
                self.after(33, self._animate_viz)
                return
            canvas.delete("all")
            is_playing = self._audio.is_playing()
            has_spectrum = any(v > 0.01 for v in self._viz_spectrum)
            
            # Use real spectrum data when available
            if is_playing and has_spectrum:
                for i in range(self._viz_bars):
                    self._viz_heights[i] *= 0.85
                    target = self._viz_targets[i] if i < len(self._viz_targets) else 0.1
                    self._viz_heights[i] = max(self._viz_heights[i], target * 0.9)
            else:
                t = self._viz_pos_s
                for i in range(self._viz_bars):
                    fi = i / max(self._viz_bars - 1, 1)
                    v = (math.sin(t * 2.8 + fi * 6.3) * 0.28 + math.sin(t * 4.1 + fi * 10.5) * 0.16 +
                        math.sin(t * 1.3 + fi * 3.7) * 0.18 + random.uniform(-0.03, 0.03))
                    self._viz_targets[i] = min(0.95, max(0.04, 0.20 + v))
                rise = 0.30 if is_playing else 0.07
                fall = 0.13 if is_playing else 0.04
                for i in range(self._viz_bars):
                    d = self._viz_targets[i] - self._viz_heights[i]
                    self._viz_heights[i] += d * (rise if d > 0 else fall)
            
            color = self._viz_color
            mode = getattr(self, "_viz_color_mode", tk.StringVar(value="accent"))
            cmode = mode.get() if isinstance(mode, tk.StringVar) else "accent"
            if cmode == "white": color = "#ffffff"
            elif cmode == "dynamic" and hasattr(self, "_art_dynamic_color"): color = self._art_dynamic_color
            
            style_var = getattr(self, "_viz_style", None)
            style = style_var.get() if isinstance(style_var, tk.StringVar) else "bars"
            if style == "bars": self._draw_viz_bars(canvas, cw, ch, color)
            elif style == "wave": self._draw_viz_wave(canvas, cw, ch, color)
            elif style == "spectrum": self._draw_viz_spectrum(canvas, cw, ch, color)
            else: self._draw_viz_bars(canvas, cw, ch, color)
        except: pass
        self.after(33, self._animate_viz)
    
    def _draw_viz_bars(self, canvas, cw, ch, color):
        n, gap = self._viz_bars, 3
        bar_w = max(3, (cw - gap * (n + 1)) / n)
        r = min(bar_w / 2, 5)
        for i in range(n):
            bh = max(2, self._viz_heights[i] * (ch - 40))
            x0, x1 = gap + i * (bar_w + gap), gap + i * (bar_w + gap) + bar_w
            y1, y0 = ch - 4, ch - 4 - bh
            canvas.create_rectangle(x0, y0 + r, x1, y1, fill=color, outline="")
            if r > 1: canvas.create_oval(x0, y0, x1, y0 + r * 2, fill=color, outline="")
    
    def _draw_viz_wave(self, canvas, cw, ch, color):
        n, cy = self._viz_bars, ch / 2
        pts = [(i / (n - 1) * cw, cy - self._viz_heights[i] * ch * 0.44) for i in range(n)]
        bot = [(x, cy + self._viz_heights[n - 1 - i] * ch * 0.44) for i, (x, _) in enumerate(reversed(pts))]
        if len(pts) + len(bot) >= 4:
            canvas.create_polygon([c for p in pts + bot for c in p], fill=color, outline="", smooth=True)
    
    def _draw_viz_spectrum(self, canvas, cw, ch, color):
        n, gap, cy = self._viz_bars, 3, ch // 2
        bar_w = max(3, (cw - gap * (n + 1)) / n)
        for i in range(n):
            half_h = max(2, self._viz_heights[i] * (cy - 6))
            x0, x1 = gap + i * (bar_w + gap), gap + i * (bar_w + gap) + bar_w
            canvas.create_rectangle(x0, cy - half_h, x1, cy + half_h, fill=color, outline="")
        canvas.create_line(0, cy, cw, cy, fill="#555555", width=1)
    
    def _update_viz_pos(self, pos): self._viz_pos_s = pos
    def _update_viz_color(self, color):
        self._viz_color = color
        self._art_dynamic_color = color
    
    # ==================== PLAYLISTS ====================
    def _create_playlist_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("New Playlist")
        dialog.geometry("400x150")
        dialog.transient(self)
        dialog.grab_set()
        ctk.CTkLabel(dialog, text="Playlist Name:", font=_font(13)).pack(pady=(20, 10))
        name_var = tk.StringVar()
        entry = ctk.CTkEntry(dialog, textvariable=name_var, width=300, font=_font(13))
        entry.pack(pady=10)
        entry.focus()
        def create():
            name = name_var.get().strip()
            if self._playlists.create_playlist(name):
                self._status(f"Created: {name}")
                self._refresh_playlists()
                dialog.destroy()
            else: self._status("Playlist exists or invalid name")
        ctk.CTkButton(dialog, text="Create", command=create, font=_font(12)).pack(pady=10)
    
    def _refresh_playlists(self):
        for w in self._playlists_list.winfo_children(): w.destroy()
        self._playlist_tracks_frame.grid_remove()
        self._playlists_list.grid()
        t = self._theme
        for name in self._playlists.get_all_playlists():
            tracks = self._playlists.get_playlist(name)
            row = ctk.CTkFrame(self._playlists_list, corner_radius=16, height=56, fg_color="#1c1c1e")
            row.pack(fill="x", padx=8, pady=2)
            row.pack_propagate(False)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=f"📁 {name} ({len(tracks)})", anchor="w", font=_font(13)
                ).grid(row=0, column=0, sticky="ew", padx=12, pady=12)
            ctk.CTkButton(row, text="▶ Play", width=70, height=28, corner_radius=14, font=_font(11),
                command=lambda n=name: self._play_playlist(n)).grid(row=0, column=1, padx=4)
            ctk.CTkButton(row, text="📋 View", width=60, height=28, corner_radius=14, font=_font(11),
                fg_color=t["btn_secondary"], text_color=t["btn_secondary_text"],
                command=lambda n=name: self._view_playlist(n)).grid(row=0, column=2, padx=(0, 8))
            if name != "Liked":
                ctk.CTkButton(row, text="✕", width=30, height=28, corner_radius=14, font=_font(11),
                    fg_color="#3a0a0a", text_color="#ff6b6b",
                    command=lambda n=name: self._delete_playlist(n)).grid(row=0, column=3, padx=(0, 8))
    
    def _view_playlist(self, name):
        self._current_playlist = name
        self._playlists_list.grid_remove()
        self._playlist_tracks_frame.grid()
        for w in self._playlist_tracks_frame.winfo_children(): w.destroy()
        tracks = self._playlists.get_playlist(name)
        t = self._theme
        hdr = ctk.CTkFrame(self._playlist_tracks_frame, fg_color="transparent", height=56)
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 0))
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hdr, text=f"📁 {name}", font=_font(28, "bold"), text_color=t["text"], anchor="w"
            ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(hdr, text="← Back", width=72, height=32, corner_radius=16, font=_font(12),
            command=self._refresh_playlists, fg_color=t["btn_secondary"],
            text_color=t["btn_secondary_text"]).grid(row=0, column=1, sticky="e")
        list_frame = ctk.CTkScrollableFrame(self._playlist_tracks_frame, corner_radius=0, fg_color="transparent")
        list_frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
        self._playlist_tracks_frame.grid_rowconfigure(1, weight=1)
        self._playlist_tracks_frame.grid_columnconfigure(0, weight=1)
        for i, tr in enumerate(tracks):
            row = ctk.CTkFrame(list_frame, corner_radius=16, height=56, fg_color="#1c1c1e")
            row.pack(fill="x", padx=8, pady=2)
            row.pack_propagate(False)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(row, text=f"{i+1}.", width=28, font=_font(11)).grid(row=0, column=0, padx=(10, 4), pady=12)
            ctk.CTkLabel(row, text=tr.display_name(), anchor="w", font=_font(12)
                ).grid(row=0, column=1, sticky="ew", padx=4)
            bf = ctk.CTkFrame(row, fg_color="transparent")
            bf.grid(row=0, column=2, padx=6)
            ctk.CTkButton(bf, text="▶", width=36, height=28, corner_radius=14, font=_font(11),
                command=lambda _t=tr: self._play_track(_t)).pack(side="left", padx=1)
            ctk.CTkButton(bf, text="✕", width=30, height=28, corner_radius=14, font=_font(11),
                fg_color="#3a0a0a", text_color="#ff6b6b",
                command=lambda _t=tr: self._remove_from_playlist(name, _t)).pack(side="left", padx=1)
    
    def _play_playlist(self, name):
        tracks = self._playlists.get_playlist(name)
        if tracks:
            self._queue.set_tracks(tracks, 0)
            self._play_track(tracks[0])
    
    def _remove_from_playlist(self, name, track):
        self._playlists.remove_from_playlist(name, track.video_id)
        self._view_playlist(name)
        self._status(f"Removed from {name}")
    
    def _delete_playlist(self, name):
        if self._playlists.delete_playlist(name):
            self._status(f"Deleted: {name}")
            self._refresh_playlists()
    
    # ==================== DOWNLOADS ====================
    def _refresh_downloads(self):
        for w in self._downloads_list.winfo_children(): w.destroy()
        downloads = self._downloads.get_all_downloads()
        if not downloads:
            ctk.CTkLabel(self._downloads_list, text="No downloads yet", font=_font(13)).pack(pady=40)
            return
        t = self._theme
        for tr in downloads:
            row = ctk.CTkFrame(self._downloads_list, corner_radius=16, height=56, fg_color="#1c1c1e")
            row.pack(fill="x", padx=8, pady=2)
            row.pack_propagate(False)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=tr.display_name(), anchor="w", font=_font(12)
                ).grid(row=0, column=0, sticky="ew", padx=12, pady=12)
            ctk.CTkButton(row, text="▶", width=36, height=28, corner_radius=14, font=_font(11),
                command=lambda _t=tr: self._play_track(_t)).grid(row=0, column=1, padx=4)
            ctk.CTkButton(row, text="✕", width=36, height=28, corner_radius=14, font=_font(11),
                fg_color="#3a0a0a", text_color="#ff6b6b",
                command=lambda _t=tr: self._remove_download(_t)).grid(row=0, column=2, padx=(0, 8))
    
    def _remove_download(self, track):
        if self._downloads.remove_download(track.video_id):
            self._status(f"Removed download: {track.title}")
            self._refresh_downloads()
    
    # ==================== QUEUE ====================
    def _queue_track(self, track):
        self._queue.add(track)
        self._status("Added to queue: " + track.display_name())
        if self._active_tab.get() == "queue": self._refresh_queue()
    
    def _refresh_queue(self):
        for w in self._queue_list.winfo_children(): w.destroy()
        tracks = self._queue.all()
        if not tracks:
            ctk.CTkLabel(self._queue_list, text="Queue is empty", font=_font(13)).pack(pady=40)
            return
        cur = self._queue.current_index()
        t = self._theme
        for i, tr in enumerate(tracks):
            row = ctk.CTkFrame(self._queue_list, corner_radius=16, height=56, fg_color="#1c1c1e")
            row.pack(fill="x", padx=8, pady=2)
            row.pack_propagate(False)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(row, text="▶ " if i == cur else f"{i+1}.", width=28, font=_font(12)
                ).grid(row=0, column=0, padx=(10, 4), pady=12)
            ctk.CTkLabel(row, text=tr.display_name(), anchor="w", font=_font(12)
                ).grid(row=0, column=1, sticky="ew", padx=4)
            bf = ctk.CTkFrame(row, fg_color="transparent")
            bf.grid(row=0, column=2, padx=6)
            ctk.CTkButton(bf, text="▶", width=36, height=28, corner_radius=14, font=_font(11),
                command=lambda _t=tr, _i=i: (self._queue.set_tracks(self._queue.all(), _i), self._play_track(_t))
                ).pack(side="left", padx=1)
            ctk.CTkButton(bf, text="✕", width=30, height=28, corner_radius=14, font=_font(11),
                fg_color=t["btn_secondary"], text_color=t["text_secondary"],
                command=lambda _i=i: (self._queue.remove(_i), self._refresh_queue())).pack(side="left", padx=1)
    
    def _clear_queue(self): self._queue.clear(); self._refresh_queue()
    
    # ==================== HISTORY ====================
    def _refresh_history(self):
        for w in self._history_list.winfo_children(): w.destroy()
        tracks = self._history.all()
        if not tracks:
            ctk.CTkLabel(self._history_list, text="Nothing played yet", font=_font(13)).pack(pady=40)
            return
        t = self._theme
        for tr in tracks:
            row = ctk.CTkFrame(self._history_list, corner_radius=16, height=56, fg_color="#1c1c1e")
            row.pack(fill="x", padx=8, pady=2)
            row.pack_propagate(False)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=tr.display_name(), anchor="w", font=_font(12)
                ).grid(row=0, column=0, sticky="ew", padx=12, pady=12)
            ctk.CTkButton(row, text="▶", width=36, height=28, corner_radius=14, font=_font(11),
                command=lambda _t=tr: self._play_track(_t)).grid(row=0, column=1, padx=4)
            ctk.CTkButton(row, text="+", width=36, height=28, corner_radius=14, font=_font(11),
                fg_color=t["btn_secondary"], text_color=t["btn_secondary_text"],
                command=lambda _t=tr: self._queue_track(_t)).grid(row=0, column=2, padx=(0, 8))
    
    def _clear_history(self):
        self._history.clear()
        self._refresh_history()
        self._refresh_history_sidebar()
    
    def _refresh_history_sidebar(self):
        for w in self._history_sidebar.winfo_children(): w.destroy()
        t = self._theme
        for tr in self._history.all()[:8]:
            btn = ctk.CTkButton(self._history_sidebar, text=tr.title or "Unknown", height=32,
                anchor="w", corner_radius=6, font=_font(11), fg_color="transparent",
                text_color=t["text_secondary"], hover_color=t["bg_hover"],
                command=lambda _t=tr: self._play_track(_t))
            btn.pack(fill="x", padx=2, pady=1)
    
    # ==================== LYRICS ====================
    def _show_synced_lyrics(self, lines):
        self._synced_lines = lines
        self._active_lyric = -1
        self._np_lyrics_plain.pack_forget()
        for lbl in self._lyric_labels: lbl.destroy()
        self._lyric_labels.clear()
        t = self._theme
        for _, text in lines:
            lbl = ctk.CTkLabel(self._synced_scroll, text=text, font=_font(14),
                text_color=t["lyric_inactive"], anchor="w", wraplength=360)
            lbl.pack(fill="x", padx=16, pady=3, anchor="w")
            self._lyric_labels.append(lbl)
    
    def _show_plain_lyrics(self, text):
        self._synced_lines = []
        for lbl in self._lyric_labels: lbl.destroy()
        self._lyric_labels.clear()
        self._np_lyrics_plain.configure(text=text)
        self._np_lyrics_plain.pack(padx=12, pady=12, anchor="w")
        self._lyrics_text.configure(state="normal")
        self._lyrics_text.delete("1.0", "end")
        self._lyrics_text.insert("end", text)
        self._lyrics_text.configure(state="disabled")
    
    def _highlight_lyric(self, pos_s):
        if not self._synced_lines or not self._lyric_labels: return
        active = -1
        for i, (ts, _) in enumerate(self._synced_lines):
            if ts <= pos_s: active = i
            else: break
        if active == self._active_lyric: return
        self._active_lyric = active
        t = self._theme
        for i, lbl in enumerate(self._lyric_labels):
            if i == active: lbl.configure(text_color=t["lyric_active"], font=_font(15, "bold"))
            else: lbl.configure(text_color=t["lyric_inactive"], font=_font(14))
    
    def _load_lyrics(self, track):
        fetch_lyrics_async(track.artist, track.title,
            lambda text: self.after(0, lambda: self._show_plain_lyrics(text)),
            lambda lines: self.after(0, lambda: self._show_synced_lyrics(lines)))
    
    # ==================== SETTINGS ====================
    def _build_settings_tab(self):
        import platform
        t = self._theme
        self._settings_frame = ctk.CTkFrame(self._tab_content, corner_radius=0, fg_color="transparent")
        self._settings_frame.grid_columnconfigure(0, weight=1)
        scroll = ctk.CTkScrollableFrame(self._settings_frame, corner_radius=0, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew", padx=24, pady=16)
        self._settings_frame.grid_rowconfigure(0, weight=1)
        scroll.grid_columnconfigure(0, weight=1)
        def section(parent, title):
            ctk.CTkLabel(parent, text=title, font=_font(13, "bold"),
                text_color=t["accent_light"], anchor="w").pack(fill="x", pady=(18, 6))
            ctk.CTkFrame(parent, height=1, fg_color=t["bg_hover"]).pack(fill="x")
        ctk.CTkLabel(scroll, text="Settings", font=_font(28, "bold"),
            text_color=t["text"], anchor="w").pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(scroll, text="Customize Streamy", font=_font(13),
            text_color=t["text_muted"], anchor="w").pack(fill="x", pady=(0, 8))
        section(scroll, "VISUALIZER")
        viz_row = ctk.CTkFrame(scroll, fg_color=t["card_bg"], corner_radius=10)
        viz_row.pack(fill="x", pady=3)
        viz_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(viz_row, text="Visualizer Style", font=_font(13, "bold"),
            text_color=t["text"], anchor="w").grid(row=0, column=0, padx=16, pady=10, sticky="w")
        self._viz_style = tk.StringVar(value="bars")
        for style, label in [("bars", "Bars"), ("wave", "Wave"), ("spectrum", "Spectrum")]:
            ctk.CTkRadioButton(viz_row, text=label, variable=self._viz_style, value=style,
                font=_font(12), text_color=t["text"], fg_color=t["accent"],
                hover_color=t["accent_light"]).grid(row=1 if style=="bars" else 2 if style=="wave" else 3,
                    column=0, padx=24, pady=2, sticky="w")
        section(scroll, "DATA")
        hist_row = ctk.CTkFrame(scroll, fg_color=t["card_bg"], corner_radius=10)
        hist_row.pack(fill="x", pady=3)
        ctk.CTkLabel(hist_row, text="Clear History", font=_font(13, "bold"),
            text_color=t["text"], anchor="w").grid(row=0, column=0, padx=16, pady=10, sticky="w")
        ctk.CTkButton(hist_row, text="Clear", width=100, height=32, corner_radius=16, font=_font(11),
            fg_color="#3a0a0a", text_color="#ff6b6b",
            command=lambda: (self._clear_history(), self._status("History cleared"))
            ).grid(row=0, column=1, padx=16, pady=10)
        section(scroll, "ABOUT")
        about_row = ctk.CTkFrame(scroll, fg_color=t["card_bg"], corner_radius=10)
        about_row.pack(fill="x", pady=3)
        ctk.CTkLabel(about_row, text=f"Streamy · Python {platform.python_version()}",
            font=_font(11), text_color=t["text_muted"], anchor="w").pack(padx=16, pady=10)
    
    def _apply_theme(self):
        t = self._theme
        try:
            self.configure(fg_color=t["bg"])
            self._sb.configure(fg_color=t["sidebar_bg"])
            self._pb.configure(fg_color=t["player_bg"])
            self._sbar.configure(fg_color=t["status_bg"])
            self._content.configure(fg_color=t["bg"])
            self._logo.configure(text_color=t["accent_light"])
            self._search_btn.configure(fg_color=t["btn_primary"], text_color=t["btn_text"])
            self._play_btn.configure(fg_color=t["btn_primary"], text_color=t["btn_text"])
            self._seek_bar.configure(button_color=t["btn_primary"], progress_color=t["btn_primary"])
        except: pass
        self._switch_tab(self._active_tab.get())
    
    def _status(self, msg): self._status_lbl.configure(text=msg)

# Entry Point
if __name__ == "__main__":
    app = StreamyApp()
    app.mainloop()