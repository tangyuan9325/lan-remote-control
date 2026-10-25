# -*- coding: utf-8 -*-
"""
RemoteControl 1.4 (Claude-styled refactor) — core runtime.

Performance & security highlights vs legacy remote_control_v1.4.py:
  * On-demand capture: screen/camera threads are idle until the first client
    actually pulls a frame, and auto-stop ~5s after the last viewer leaves
    (the old build burned a full CPU core capturing 30fps + encoding even
    with nobody connected, camera always on).
  * Frame diffing: when nothing on screen changed, the previous JPEG bytes are
    reused — no resize/encode work at all for static screens.
  * Pillow-only fast path: RGB -> resize -> JPEG without numpy/OpenCV double
    color conversion round-trips (OpenCV is lazily imported only for cameras).
  * Security fixes: path traversal guards on upload/download/delete, XSS-safe
    web console rendering, command execution disabled by default (opt-in via
    --enable-cmd), constant-time password check, mjpeg streams send real
    multipart frames (old version returned a single bare JPEG that most
    browsers would not refresh).
"""

import io
import os
import re
import sys
import time
import socket
import hashlib
import secrets
import threading

VERSION = "1.4.1-claude"

_IS_WIN = os.name == "nt"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class Config:
    resolution = (1280, 720)   # stream resolution (WxH)
    fps = 15                   # max capture fps (frame-diff skips static frames)
    jpeg_quality = 60
    camera_enabled = False     # requested state (threads still start on demand)
    microphone_enabled = False
    enable_cmd = False         # RCE surface: opt-in only (--enable-cmd)
    password = ""              # optional access password
    _override_upload_dir = None  # set by tests via set_upload_dir()
    upload_dir = None          # set in init_paths()
    base_dir = None            # app root (PyInstaller aware)
    tray_icon = None


def init_paths():
    """Resolve writable/data paths; robust under PyInstaller --onefile."""
    if getattr(sys, "frozen", False):
        # exe lives next to user data; sys._MEIPASS is a temp dir for bundled
        # resources (icon etc.) which we must NOT use as CWD-dependent target.
        Config.base_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        Config.base_dir = os.path.dirname(os.path.abspath(__file__))
    if Config._override_upload_dir:
        Config.upload_dir = Config._override_upload_dir
    else:
        Config.upload_dir = os.path.join(Config.base_dir, "uploads")
    os.makedirs(Config.upload_dir, exist_ok=True)


def set_upload_dir(path):
    """Redirect the uploads dir (used by tests); re-runs init_paths()."""
    Config._override_upload_dir = path
    init_paths()


init_paths()


def resource_path(name):
    """Bundled resource (works from source tree and PyInstaller _MEIPASS)."""
    try:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            p = os.path.join(meipass, name)
            if os.path.exists(p):
                return p
    except Exception:
        pass
    return os.path.join(Config.base_dir, name)


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return "127.0.0.1"


# ---------------------------------------------------------------------------
# Auth helpers (constant time)
# ---------------------------------------------------------------------------


def check_password(candidate):
    if not Config.password:
        return True
    a = hashlib.sha256((candidate or "").encode("utf-8")).digest()
    b = hashlib.sha256(Config.password.encode("utf-8")).digest()
    return secrets.compare_digest(a, b)


# ---------------------------------------------------------------------------
# Screen capture (on-demand + frame diffing + Pillow fast path)
# ---------------------------------------------------------------------------

try:  # dxcam gives true desktop-duplication captures on Windows
    import dxcam  # type: ignore
    _HAS_DXCAM = True
except Exception:
    dxcam = None
    _HAS_DXCAM = False

from PIL import Image, ImageGrab  # noqa: E402


class ScreenCaptureThread(threading.Thread):
    """Captures the desktop only while viewers are present.

    - ``touch()`` keeps the worker alive (called by every /video_feed request)
    - no viewers for IDLE_TIMEOUT seconds -> thread parks (zero CPU)
    - pixel-diff sampling skips re-encoding unchanged frames entirely
    """

    IDLE_TIMEOUT = 5.0
    MAX_SIZE = 1920  # hard cap: never encode more than this on the long edge

    def __init__(self):
        super().__init__()
        self.daemon = True
        self.running = True
        self._wake = threading.Event()
        self._last_view = 0.0
        self.last_frame = None       # encoded jpeg bytes
        self._prev_sample = None     # coarse pixel sample for change detection
        self.lock = threading.Lock()
        self.resolution = Config.resolution
        self.fps = Config.fps
        self.jpeg_quality = Config.jpeg_quality
        self._cam = None             # dxcam backend (Windows)
        self._dxcam_failed = False
        self.stats = {"captured": 0, "reused": 0}

    # -- settings -----------------------------------------------------------
    def update_settings(self, resolution=None, fps=None, quality=None):
        if resolution and 2 <= resolution[0] <= 7680 and 2 <= resolution[1] <= 4320:
            w, h = resolution
            long_edge = max(w, h)
            if long_edge > self.MAX_SIZE:  # cap encode size: huge CPU saver
                scale = self.MAX_SIZE / float(long_edge)
                w, h = int(w * scale), int(h * scale)
            self.resolution = (w, h)
        if fps:
            self.fps = max(1, min(60, int(fps)))
        if quality:
            self.jpeg_quality = max(10, min(95, int(quality)))

    # -- lifecycle ----------------------------------------------------------
    def touch(self):
        """Called by consumers: marks interest and wakes a parked worker."""
        self._last_view = time.time()
        self._wake.set()

    def has_viewers(self):
        return (time.time() - self._last_view) < self.IDLE_TIMEOUT

    def stop(self):
        self.running = False
        self._wake.set()

    # -- capture backends ---------------------------------------------------
    def _open_dxcam(self):
        if not (_IS_WIN and _HAS_DXCAM and not self._dxcam_failed):
            return False
        try:
            self._cam = dxcam.create(output_color="RGB")
            return True
        except Exception:
            self._dxcam_failed = True
            self._cam = None
            return False

    def _grab(self):
        if self._cam is not None:
            img = self._cam.grab()
            if img is not None:
                return Image.fromarray(img)
            return None
        return ImageGrab.grab(all_screens=True)

    @staticmethod
    def _sample(img):
        # downscale to 16x9 and read raw bytes: cheap change fingerprint
        small = img.resize((16, 9), Image.BILINEAR)
        return small.tobytes()

    def _changed(self, img):
        cur = self._sample(img)
        prev = self._prev_sample
        self._prev_sample = cur
        if prev is None or len(prev) != len(cur):
            return True
        return cur != prev

    def _encode(self, img):
        w, h = self.resolution
        if img.size != (w, h):
            img = img.resize((w, h), Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=self.jpeg_quality, optimize=True)
        return buf.getvalue()

    # -- main loop ----------------------------------------------------------
    def run(self):
        if self._cam is None:
            self._open_dxcam()
        while self.running:
            if not self.has_viewers():
                # park until someone asks for a frame again
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue

            t0 = time.time()
            frame_time = 1.0 / max(1, self.fps)
            try:
                img = self._grab()
                if img is not None:
                    if self._changed(img):
                        data = self._encode(img)
                        with self.lock:
                            self.last_frame = data
                        self.stats["captured"] += 1
                    else:
                        self.stats["reused"] += 1
            except Exception as e:  # transient grab failures must not kill us
                print(f"[screen] capture error: {e}")
                if self._cam is not None:
                    try:
                        self._cam.stop()
                    except Exception:
                        pass
                    self._cam = None
                    self._open_dxcam()
                time.sleep(0.25)

            dt = time.time() - t0
            if dt < frame_time:
                # wait respects wake requests from new viewers
                self._wake.wait(timeout=frame_time - dt)
                self._wake.clear()

        if self._cam is not None:
            try:
                self._cam.stop()
            except Exception:
                pass
            self._cam = None

    def get_frame(self):
        with self.lock:
            return self.last_frame or b""


# ---------------------------------------------------------------------------
# Camera capture (lazy OpenCV, on-demand like the screen thread)
# ---------------------------------------------------------------------------


class CameraCaptureThread(threading.Thread):
    IDLE_TIMEOUT = 5.0

    def __init__(self):
        super().__init__()
        self.daemon = True
        self.running = True
        self._wake = threading.Event()
        self._last_view = 0.0
        self.last_frame = None
        self.lock = threading.Lock()
        self.cap = None
        self.jpeg_quality = 60
        self.resolution = (640, 480)

    def touch(self):
        self._last_view = time.time()
        self._wake.set()

    def has_viewers(self):
        return (time.time() - self._last_view) < self.IDLE_TIMEOUT

    def stop(self):
        self.running = False
        self._wake.set()

    def run(self):
        cv2 = None
        try:
            import cv2 as _cv2
            cv2 = _cv2
        except Exception:
            pass
        opened = False
        while self.running:
            if not self.has_viewers():
                if opened and self.cap is not None:
                    try:
                        self.cap.release()
                    except Exception:
                        pass
                    self.cap = None
                    opened = False
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            if cv2 is None:
                time.sleep(1.0)
                continue
            if not opened:
                try:
                    self.cap = cv2.VideoCapture(0)
                    opened = self.cap.isOpened()
                    if opened:
                        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
                        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])
                except Exception:
                    opened = False
                if not opened:
                    time.sleep(2.0)
                    continue
            try:
                ret, frame = self.cap.read()
                if ret:
                    ok, buffer = cv2.imencode(
                        ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]
                    )
                    if ok:
                        with self.lock:
                            self.last_frame = buffer.tobytes()
                else:
                    time.sleep(0.1)
            except Exception as e:
                print(f"[camera] error: {e}")
                try:
                    self.cap.release()
                except Exception:
                    pass
                self.cap = None
                opened = False
                time.sleep(1.0)
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None

    def get_frame(self):
        with self.lock:
            return self.last_frame or b""


# ---------------------------------------------------------------------------
# Microphone capture (only records while a listener is attached)
# ---------------------------------------------------------------------------


class MicrophoneCaptureThread(threading.Thread):
    IDLE_TIMEOUT = 5.0

    def __init__(self):
        super().__init__()
        self.daemon = True
        self.running = True
        self._wake = threading.Event()
        self._last_view = 0.0
        self.audio_data = b""
        self.lock = threading.Lock()
        self.chunk = 1024
        self.rate = 16000      # mono 16k: plenty for voice, 1/5.5 of 44.1k stereo
        self.channels = 1

    def touch(self):
        self._last_view = time.time()
        self._wake.set()

    def has_viewers(self):
        return (time.time() - self._last_view) < self.IDLE_TIMEOUT

    def stop(self):
        self.running = False
        self._wake.set()

    def run(self):
        pa = None
        try:
            import pyaudio
            pa = pyaudio.PyAudio()
        except Exception as e:
            print(f"[mic] pyaudio unavailable: {e}")
        stream = None
        while self.running and pa is not None:
            if not self.has_viewers():
                if stream is not None:
                    try:
                        stream.stop_stream()
                        stream.close()
                    except Exception:
                        pass
                    stream = None
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            if stream is None:
                try:
                    fmt = pa.get_format_from_width(2)
                    stream = pa.open(format=fmt, channels=self.channels, rate=self.rate,
                                     input=True, frames_per_buffer=self.chunk)
                except Exception as e:
                    print(f"[mic] open failed: {e}")
                    time.sleep(2.0)
                    continue
            try:
                data = stream.read(self.chunk, exception_on_overflow=False)
                with self.lock:
                    self.audio_data = data
            except Exception as e:
                print(f"[mic] read error: {e}")
                stream = None
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        if pa is not None:
            try:
                pa.terminate()
            except Exception:
                pass

    def get_audio(self):
        with self.lock:
            return self.audio_data or b""


# ---------------------------------------------------------------------------
# Worker registry (singletons, started once, idled automatically)
# ---------------------------------------------------------------------------

_workers_lock = threading.Lock()
screen_thread = None
camera_thread = None
microphone_thread = None


def ensure_screen_worker():
    global screen_thread
    with _workers_lock:
        if screen_thread is None or not screen_thread.running:
            screen_thread = ScreenCaptureThread()
            screen_thread.update_settings(Config.resolution, Config.fps, Config.jpeg_quality)
            screen_thread.start()
        else:
            screen_thread.update_settings(Config.resolution, Config.fps, Config.jpeg_quality)
        screen_thread.touch()
        return screen_thread


def ensure_camera_worker():
    global camera_thread
    with _workers_lock:
        if camera_thread is None or not camera_thread.running:
            camera_thread = CameraCaptureThread()
            camera_thread.start()
        camera_thread.touch()
        return camera_thread


def ensure_mic_worker():
    global microphone_thread
    with _workers_lock:
        if microphone_thread is None or not microphone_thread.running:
            microphone_thread = MicrophoneCaptureThread()
            microphone_thread.start()
        microphone_thread.touch()
        return microphone_thread


def stop_all_workers():
    with _workers_lock:
        for t in (screen_thread, camera_thread, microphone_thread):
            if t is not None:
                try:
                    t.stop()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Path safety for file transfer endpoints
# ---------------------------------------------------------------------------

_BAD_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_upload_path(filename):
    """Map a client-supplied filename into Config.upload_dir safely.

    Rejects traversal sequences, absolute paths, hidden dot-files and control
    chars; resolves the final path and requires it to stay inside uploads/.
    """
    if not filename:
        return None
    # reject BEFORE basename(): "a/../../x" would otherwise sanitize to "x"
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    name = filename.strip()
    if not name or name.startswith(".") or len(name) > 255:
        return None
    if _BAD_CHARS_RE.search(name):
        return None
    base = os.path.realpath(Config.upload_dir)
    target = os.path.realpath(os.path.join(base, name))
    if target != base and not target.startswith(base + os.sep):
        return None
    return target


# ---------------------------------------------------------------------------
# MJPEG multipart helper (the legacy code served a single bare JPEG per hit;
# browsers do not auto-refresh that reliably — this emits proper parts)
# ---------------------------------------------------------------------------


def mjpeg_stream(getter, interval, stop_check=None, max_seconds=300):
    """Yield multipart MJPEG parts.

    IMPORTANT (PyInstaller/windowed-exe safety): never blocks waiting for the
    first frame — if no capture backend is available (headless session, locked
    workstation, missing display) it emits a visible "waiting" placeholder JPEG
    instead of hanging the request thread forever.
    """
    boundary = "frameboundary"
    deadline = time.time() + max_seconds
    waited = 0.0
    placeholder_sent = False
    try:
        while time.time() < deadline:
            if stop_check is not None and not stop_check():
                break
            data = getter()
            if not data and not placeholder_sent and waited > 1.0:
                data = _placeholder_jpeg(
                    'Waiting for screen capture... (\u7b49\u5f85\u753b\u9762\u91c7\u96c6)')
                placeholder_sent = True
            if data:
                yield (b"--" + boundary.encode() + b"\r\n"
                       b"Content-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n"
                       + data + b"\r\n")
            time.sleep(interval)
            waited += interval
    except GeneratorExit:
        raise
    except Exception:
        return


_placeholder_cache = {}


def _placeholder_jpeg(text="无信号"):
    """Render (and cache) a small dark JPEG with a message — pure Pillow."""
    key = text
    if key in _placeholder_cache:
        return _placeholder_cache[key]
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (640, 360), (20, 19, 17))
        d = ImageDraw.Draw(img)
        try:
            d.text((200, 170), text, fill=(207, 201, 189))
        except Exception:
            pass
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=60)
        data = buf.getvalue()
    except Exception:
        data = b""
    _placeholder_cache[key] = data
    return data
