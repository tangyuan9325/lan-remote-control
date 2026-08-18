"""
QWebChannel bridge for PyQt5 viewer v1.2.
Handles: device discovery, screen, input, file transfer, voice chat.
"""
import json
import os
import socket
import threading
import base64
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QFileDialog
from protocol import MSG_JSON, MSG_JPEG, MSG_FILE, MSG_AUDIO, recv_message, send_json
from discovery import discover

try:
    import pyaudio
    _HAS_AUDIO = True
except ImportError:
    _HAS_AUDIO = False

class Bridge(QObject):
    frameReady = pyqtSignal(str)
    statusChanged = pyqtSignal(str)
    devicesFound = pyqtSignal(str)
    connectionClosed = pyqtSignal()
    fileListReceived = pyqtSignal(str)
    fileDownloadStart = pyqtSignal(str)
    fileDownloadProgress = pyqtSignal(int)
    fileDownloadComplete = pyqtSignal(str)
    fileUploadDone = pyqtSignal(str)
    fileError = pyqtSignal(str)
    voiceReady = pyqtSignal()
    voiceError = pyqtSignal(str)
    audioLevel = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self._sock = None
        self._connected = False
        self._remote_width = 0
        self._remote_height = 0
        self._recv_thread = None
        self._download_name = None
        self._download_size = 0
        self._download_data = bytearray()
        self._upload_path = None
        self._pyaudio = None
        self._record_stream = None
        self._play_stream = None
        self._recording = False
        self._playing = False
        self._play_buffer = []
        self._play_lock = threading.Lock()
        if _HAS_AUDIO:
            try:
                self._pyaudio = pyaudio.PyAudio()
            except Exception:
                self._pyaudio = None

    @pyqtSlot()
    def discoverDevices(self):
        def _run():
            try:
                devices = discover(timeout=2.0)
                self.devicesFound.emit(json.dumps(devices))
            except Exception:
                self.devicesFound.emit(json.dumps([]))
        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot(str, int, str)
    def connect(self, ip, port, password):
        def _run():
            try:
                self.statusChanged.emit(f"Connecting to {ip}:{port}...")
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._sock.settimeout(5)
                self._sock.connect((ip, port))
                self._sock.settimeout(None)
                send_json(self._sock, {"type": "hello", "password": password})
                msg_type, payload = recv_message(self._sock)
                if msg_type != MSG_JSON:
                    raise RuntimeError("Bad handshake")
                msg = json.loads(payload.decode("utf-8"))
                if msg.get("type") != "hello_ok":
                    raise RuntimeError(msg.get("reason", "Rejected"))
                self._remote_width = msg["width"]
                self._remote_height = msg["height"]
                self._connected = True
                self.statusChanged.emit(f"Connected  {self._remote_width}x{self._remote_height}")
                self._recv_loop()
            except Exception as e:
                self.statusChanged.emit(f"Error: {e}")
                self._cleanup()
        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot()
    def disconnect(self):
        self._stop_voice()
        self._cleanup()

    @pyqtSlot(str)
    def sendInput(self, event_json):
        if self._sock and self._connected:
            try:
                send_json(self._sock, json.loads(event_json))
            except Exception:
                pass

    @pyqtSlot(str)
    def listFiles(self, path):
        if self._sock and self._connected:
            try:
                send_json(self._sock, {"type": "file_list", "path": path})
            except Exception as e:
                self.fileError.emit(str(e))

    @pyqtSlot(str)
    def downloadFile(self, path):
        self._download_data = bytearray()
        self._download_name = None
        self._download_size = 0
        if self._sock and self._connected:
            try:
                send_json(self._sock, {"type": "file_download", "path": path})
            except Exception as e:
                self.fileError.emit(str(e))

    @pyqtSlot()
    def uploadFileDialog(self):
        path, _ = QFileDialog.getOpenFileName(None, "Select file to upload")
        if path and os.path.isfile(path):
            self._upload_file(path)

    def _upload_file(self, local_path, remote_dir="."):
        def _run():
            try:
                name = os.path.basename(local_path)
                size = os.path.getsize(local_path)
                send_json(self._sock, {"type": "file_upload_start", "path": remote_dir, "name": name, "size": size})
                self._upload_path = local_path
                import time
                for _ in range(50):
                    if self._upload_path is None:
                        break
                    time.sleep(0.1)
                if self._upload_path:
                    with open(local_path, "rb") as f:
                        while True:
                            chunk = f.read(64 * 1024)
                            if not chunk:
                                break
                            self._send_file_chunk(chunk)
                    send_json(self._sock, {"type": "file_upload_complete", "name": name})
                    self._upload_path = None
            except Exception as e:
                self.fileError.emit(str(e))
        threading.Thread(target=_run, daemon=True).start()

    def _send_file_chunk(self, data):
        if self._sock and self._connected:
            import struct
            header = struct.pack(">BI", MSG_FILE, len(data))
            try:
                self._sock.sendall(header + data)
            except Exception:
                pass

    @pyqtSlot()
    def startVoice(self):
        if not self._pyaudio:
            self.voiceError.emit("pyaudio not installed")
            return
        if self._sock and self._connected:
            try:
                send_json(self._sock, {"type": "voice_start"})
            except Exception as e:
                self.voiceError.emit(str(e))

    @pyqtSlot()
    def stopVoice(self):
        self._stop_voice()
        if self._sock and self._connected:
            try:
                send_json(self._sock, {"type": "voice_stop"})
            except Exception:
                pass

    def _stop_voice(self):
        self._recording = False
        self._playing = False
        if self._record_stream:
            try:
                self._record_stream.stop_stream()
                self._record_stream.close()
            except Exception:
                pass
            self._record_stream = None
        if self._play_stream:
            try:
                self._play_stream.stop_stream()
                self._play_stream.close()
            except Exception:
                pass
            self._play_stream = None
        with self._play_lock:
            self._play_buffer.clear()

    def _start_recording(self):
        if not self._pyaudio or self._recording:
            return
        self._recording = True
        def _record():
            try:
                self._record_stream = self._pyaudio.open(
                    format=self._pyaudio.get_format_from_width(2),
                    channels=1, rate=16000, input=True, frames_per_buffer=1024,
                )
                while self._recording:
                    try:
                        data = self._record_stream.read(1024, exception_on_overflow=False)
                        import struct
                        header = struct.pack(">BI", MSG_AUDIO, len(data))
                        if self._sock and self._connected:
                            self._sock.sendall(header + data)
                    except Exception:
                        break
            except Exception as e:
                self.voiceError.emit(str(e))
            finally:
                if self._record_stream:
                    try:
                        self._record_stream.close()
                    except Exception:
                        pass
                    self._record_stream = None
        threading.Thread(target=_record, daemon=True).start()

    def _play_audio(self, data):
        if not self._pyaudio:
            return
        with self._play_lock:
            self._play_buffer.append(data)
        if not self._playing:
            self._playing = True
            def _play():
                try:
                    self._play_stream = self._pyaudio.open(
                        format=self._pyaudio.get_format_from_width(2),
                        channels=1, rate=16000, output=True, frames_per_buffer=1024,
                    )
                    while self._playing:
                        with self._play_lock:
                            if self._play_buffer:
                                chunk = self._play_buffer.pop(0)
                            else:
                                chunk = None
                        if chunk:
                            try:
                                self._play_stream.write(chunk)
                            except Exception:
                                break
                        else:
                            import time
                            time.sleep(0.01)
                except Exception:
                    pass
                finally:
                    self._playing = False
                    if self._play_stream:
                        try:
                            self._play_stream.close()
                        except Exception:
                            pass
                        self._play_stream = None
            threading.Thread(target=_play, daemon=True).start()

    def _recv_loop(self):
        while self._connected:
            try:
                msg_type, payload = recv_message(self._sock)
            except (ConnectionError, OSError):
                break
            if msg_type == MSG_JPEG:
                b64 = base64.b64encode(payload).decode("ascii")
                self.frameReady.emit(b64)
            elif msg_type == MSG_FILE:
                if self._download_name:
                    self._download_data.extend(payload)
                    self.fileDownloadProgress.emit(len(self._download_data))
            elif msg_type == MSG_AUDIO:
                self._play_audio(payload)
            elif msg_type == MSG_JSON:
                try:
                    msg = json.loads(payload.decode("utf-8"))
                    t = msg.get("type")
                    if t == "file_list_response":
                        self.fileListReceived.emit(json.dumps(msg))
                    elif t == "file_list_error":
                        self.fileError.emit(msg.get("error", "List error"))
                    elif t == "file_download_start":
                        self._download_name = msg.get("name", "file")
                        self._download_size = msg.get("size", 0)
                        self._download_data = bytearray()
                        self.fileDownloadStart.emit(json.dumps(msg))
                    elif t == "file_download_complete":
                        self._save_download()
                    elif t == "file_download_error":
                        self.fileError.emit(msg.get("error", "Download error"))
                        self._download_name = None
                        self._download_data = bytearray()
                    elif t == "file_upload_ready":
                        if self._upload_path and os.path.isfile(self._upload_path):
                            local = self._upload_path
                            self._upload_path = None
                            def _send_chunks():
                                try:
                                    with open(local, "rb") as f:
                                        while True:
                                            chunk = f.read(64 * 1024)
                                            if not chunk:
                                                break
                                            self._send_file_chunk(chunk)
                                    name = os.path.basename(local)
                                    send_json(self._sock, {"type": "file_upload_complete", "name": name})
                                except Exception as e:
                                    self.fileError.emit(str(e))
                            threading.Thread(target=_send_chunks, daemon=True).start()
                    elif t == "file_upload_done":
                        self.fileUploadDone.emit(json.dumps(msg))
                    elif t == "file_upload_error":
                        self.fileError.emit(msg.get("error", "Upload error"))
                    elif t == "voice_ready":
                        self._start_recording()
                        self.voiceReady.emit()
                    elif t == "voice_error":
                        self.voiceError.emit(msg.get("error", "Voice error"))
                except Exception:
                    pass
        self._stop_voice()
        self._cleanup()

    def _save_download(self):
        if not self._download_name:
            return
        try:
            save_dir = os.path.join(os.path.expanduser("~"), "Downloads")
            os.makedirs(save_dir, exist_ok=True)
            save_path = os.path.join(save_dir, self._download_name)
            base, ext = os.path.splitext(save_path)
            i = 1
            while os.path.exists(save_path):
                save_path = f"{base}_{i}{ext}"
                i += 1
            with open(save_path, "wb") as f:
                f.write(bytes(self._download_data))
            self.fileDownloadComplete.emit(json.dumps({
                "name": self._download_name,
                "size": len(self._download_data),
                "local_path": save_path,
            }))
        except Exception as e:
            self.fileError.emit(f"Save error: {e}")
        finally:
            self._download_name = None
            self._download_data = bytearray()

    def _cleanup(self):
        self._connected = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self.connectionClosed.emit()
