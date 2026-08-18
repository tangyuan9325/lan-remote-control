"""
Screen capture using mss + Pillow.
Captures the primary monitor and encodes to JPEG.
Thread-safe: each calling thread gets its own mss instance via threading.local.
Optimized for low-latency streaming.
"""
import io
import threading
import mss
from PIL import Image


class ScreenCapture:
    def __init__(self, quality: int = 50, scale: float = 1.0):
        self.quality = quality
        self.scale = scale
        self._local = threading.local()
        # Read monitor geometry once on the main thread
        with mss.mss() as sct:
            self._monitor = sct.monitors[1]  # primary monitor

    @property
    def size(self):
        w = self._monitor["width"]
        h = self._monitor["height"]
        if self.scale < 1.0:
            w = int(w * self.scale)
            h = int(h * self.scale)
        return (w, h)

    def set_quality(self, q: int):
        self.quality = max(10, min(100, int(q)))

    def _get_sct(self):
        """Return a thread-local mss instance, creating one if needed."""
        if not hasattr(self._local, "sct"):
            self._local.sct = mss.mss()
        return self._local.sct

    def capture_jpeg(self) -> bytes:
        """Grab the screen and return JPEG-encoded bytes (fast path)."""
        sct = self._get_sct()
        img = sct.grab(self._monitor)
        # mss returns BGRA; convert to RGB for JPEG
        pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")

        # Downscale if needed for performance
        if self.scale < 1.0:
            new_w = int(pil_img.width * self.scale)
            new_h = int(pil_img.height * self.scale)
            pil_img = pil_img.resize((new_w, new_h), Image.BILINEAR)

        buf = io.BytesIO()
        # Fast JPEG: no optimize, use chroma subsampling for smaller files
        pil_img.save(
            buf,
            format="JPEG",
            quality=self.quality,
            optimize=False,
            progressive=False,
            subsampling=2,  # 4:2:0 chroma subsampling
        )
        return buf.getvalue()

    def close(self):
        if hasattr(self._local, "sct"):
            try:
                self._local.sct.close()
            except Exception:
                pass
