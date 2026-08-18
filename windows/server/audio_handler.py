"""
Audio handler for voice chat.
Records microphone input and plays received audio.
Format: PCM 16-bit signed, 16000 Hz, mono.
"""
import threading
import struct

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
CHUNK = 1024

class AudioHandler:
    def __init__(self, send_audio_fn):
        self._send_audio = send_audio_fn
        self._recording = False
        self._playing = False
        self._record_thread = None
        self._play_thread = None
        self._play_buffer = []
        self._buffer_lock = threading.Lock()
        self._pyaudio = None
        self._record_stream = None
        self._play_stream = None
        self._available = False
        try:
            import pyaudio
            self._pyaudio = pyaudio.PyAudio()
            self._available = True
        except ImportError:
            print("[Audio] pyaudio not installed, voice chat unavailable")
        except Exception as e:
            print(f"[Audio] init failed: {e}")

    @property
    def available(self):
        return self._available

    def start_recording(self):
        if not self._available or self._recording:
            return
        self._recording = True
        self._record_thread = threading.Thread(target=self._record_loop, daemon=True)
        self._record_thread.start()

    def stop_recording(self):
        self._recording = False

    def _record_loop(self):
        try:
            self._record_stream = self._pyaudio.open(
                format=self._pyaudio.get_format_from_width(SAMPLE_WIDTH),
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK,
            )
            while self._recording:
                try:
                    data = self._record_stream.read(CHUNK, exception_on_overflow=False)
                    self._send_audio(data)
                except Exception:
                    break
        except Exception as e:
            print(f"[Audio] record error: {e}")
        finally:
            if self._record_stream:
                try:
                    self._record_stream.stop_stream()
                    self._record_stream.close()
                except Exception:
                    pass
                self._record_stream = None

    def play_audio(self, data: bytes):
        if not self._available:
            return
        with self._buffer_lock:
            self._play_buffer.append(data)
        if not self._playing:
            self._start_playback()

    def _start_playback(self):
        self._playing = True
        self._play_thread = threading.Thread(target=self._play_loop, daemon=True)
        self._play_thread.start()

    def _play_loop(self):
        try:
            self._play_stream = self._pyaudio.open(
                format=self._pyaudio.get_format_from_width(SAMPLE_WIDTH),
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                output=True,
                frames_per_buffer=CHUNK,
            )
            while self._playing:
                with self._buffer_lock:
                    if self._play_buffer:
                        data = self._play_buffer.pop(0)
                    else:
                        data = None
                if data:
                    try:
                        self._play_stream.write(data)
                    except Exception:
                        break
                else:
                    import time
                    time.sleep(0.01)
        except Exception as e:
            print(f"[Audio] play error: {e}")
        finally:
            self._playing = False
            if self._play_stream:
                try:
                    self._play_stream.stop_stream()
                    self._play_stream.close()
                except Exception:
                    pass
                self._play_stream = None

    def stop(self):
        self._recording = False
        self._playing = False
        with self._buffer_lock:
            self._play_buffer.clear()
        if self._pyaudio:
            try:
                self._pyaudio.terminate()
            except Exception:
                pass
