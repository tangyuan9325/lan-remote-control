"""
QWebChannel bridge object exposed to JavaScript.
Handles device discovery, TCP connection, screen frame reception, and input sending.
"""

import json
import socket
import struct
import threading
import base64
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from protocol import MSG_JSON, MSG_JPEG, recv_message, send_json
from discovery import discover


class Bridge(QObject):
    """Python ↔ JavaScript bridge."""

    # Signals → JS
    frameReady = pyqtSignal(str)        # base64 JPEG
    statusChanged = pyqtSignal(str)     # status text
    devicesFound = pyqtSignal(str)      # JSON array of devices
    connectionClosed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._sock = None
        self._connected = False
        self._remote_width = 0
        self._remote_height = 0
        self._recv_thread = None

    @pyqtSlot()
    def discoverDevices(self):
        """Search LAN for devices, emit devicesFound."""
        def _run():
            try:
                devices = discover(timeout=2.0)
                self.devicesFound.emit(json.dumps(devices))
            except Exception as e:
                self.devicesFound.emit(json.dumps([]))
        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot(str, int, str)
    def connect(self, ip: str, port: int, password: str):
        """Connect to a remote server."""
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
                self.statusChanged.emit(
                    f"Connected  {self._remote_width}x{self._remote_height}"
                )
                self._recv_loop()
            except Exception as e:
                self.statusChanged.emit(f"Error: {e}")
                self._cleanup()
        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot()
    def disconnect(self):
        self._cleanup()

    @pyqtSlot(str)
    def sendInput(self, event_json: str):
        """Send a JSON input event to the server."""
        if self._sock and self._connected:
            try:
                event = json.loads(event_json)
                send_json(self._sock, event)
            except Exception:
                pass

    def _recv_loop(self):
        while self._connected:
            try:
                msg_type, payload = recv_message(self._sock)
            except (ConnectionError, OSError):
                break
            if msg_type == MSG_JPEG:
                b64 = base64.b64encode(payload).decode("ascii")
                self.frameReady.emit(b64)
        self._cleanup()

    def _cleanup(self):
        self._connected = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self.connectionClosed.emit()
