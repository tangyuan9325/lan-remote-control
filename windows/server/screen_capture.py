"""
Screen capture using mss + Pillow.
Captures the primary monitor and encodes to JPEG.
Thread-safe: each calling thread gets its own mss instance via threading.local.
"""
import io
import threading
import mss
from PIL import Image


class ScreenCapture:
    def __init__(self, quality: int = 70):
        self.quality = quality
        self._local = threading.local()
        # Read monitor geometry once on the main thread
        with mss.mss() as sct:
            self._monitor = sct.monitors[1]  # primary monitor

    @property
    def size(self):
        return (self._monitor["width"], self._monitor["height"])

    def set_quality(self, q: int):
        self.quality = max(10, min(100, int(q)))

    def _get_sct(self):
        """Return a thread-local mss instance, creating one if needed."""
        if not hasattr(self._local, "sct"):
            self._local.sct = mss.mss()
        return self._local.sct

    def capture_jpeg(self) -> bytes:
        """Grab the screen and return JPEG-encoded bytes."""
        sct = self._get_sct()
        img = sct.grab(self._monitor)
        # mss returns BGRA; convert to RGB for JPEG
        pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=self.quality, optimize=True)
        return buf.getvalue()

    def close(self):
        if hasattr(self._local, "sct"):
            try:
                self._local.sct.close()
            except Exception:
                pass
