"""
Screen capture using mss + Pillow.
Captures the primary monitor and encodes to JPEG.
"""

import io
import mss
from PIL import Image


class ScreenCapture:
    def __init__(self, quality: int = 70):
        self.quality = quality
        self._sct = mss.mss()
        self._monitor = self._sct.monitors[1]  # primary monitor

    @property
    def size(self):
        return (self._monitor["width"], self._monitor["height"])

    def set_quality(self, q: int):
        self.quality = max(10, min(100, int(q)))

    def capture_jpeg(self) -> bytes:
        """Grab the screen and return JPEG-encoded bytes."""
        img = self._sct.grab(self._monitor)
        # mss returns BGRA; convert to RGB for JPEG
        pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=self.quality, optimize=True)
        return buf.getvalue()

    def close(self):
        self._sct.close()
