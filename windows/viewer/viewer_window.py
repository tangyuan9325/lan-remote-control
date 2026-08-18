"""
Remote control viewer window.
Displays the streamed screen and forwards mouse/keyboard input.
"""

import io
import json
import socket
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk

from protocol import MSG_JSON, MSG_JPEG, recv_message, send_json


class RemoteWindow(tk.Toplevel):
    """A separate window showing the remote desktop."""

    def __init__(self, master, host: str, port: int, password: str = None):
        super().__init__(master)
        self.host = host
        self.port = port
        self.password = password or ""
        self.sock = None
        self.running = False
        self.remote_width = 0
        self.remote_height = 0
        self._image_ref = None  # prevent GC

        self.title(f"Remote - {host}:{port}")
        self.geometry("1024x768")

        # Canvas for the screen
        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Status bar
        self.status = tk.StringVar(value="Connecting...")
        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Label(bar, textvariable=self.status).pack(side=tk.LEFT, padx=8)

        # Bind input events
        self.canvas.bind("<Motion>", self._on_mouse_move)
        self.canvas.bind("<Button-1>", self._on_mouse_down)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.canvas.bind("<Button-3>", self._on_right_down)
        self.canvas.bind("<ButtonRelease-3>", self._on_right_up)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<MouseWheel>", self._on_scroll)
        self.canvas.bind("<Button-4>", lambda e: self._on_scroll(e, up=True))
        self.canvas.bind("<Button-5>", lambda e: self._on_scroll(e, up=False))
        self.bind("<KeyPress>", self._on_key_down)
        self.bind("<KeyRelease>", self._on_key_up)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._connect)

    # ---------- Connection ----------

    def _connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5)
            self.sock.connect((self.host, self.port))
            self.sock.settimeout(None)

            # Handshake
            send_json(self.sock, {"type": "hello", "password": self.password})
            msg_type, payload = recv_message(self.sock)
            if msg_type != MSG_JSON:
                raise RuntimeError("bad handshake")
            msg = json.loads(payload.decode("utf-8"))
            if msg.get("type") != "hello_ok":
                raise RuntimeError(msg.get("reason", "connection rejected"))

            self.remote_width = msg["width"]
            self.remote_height = msg["height"]
            self.status.set(f"Connected  {self.remote_width}x{self.remote_height}")
            self.running = True
            self.focus_force()
            threading.Thread(target=self._recv_loop, daemon=True).start()
        except Exception as e:
            self.status.set(f"Connection failed: {e}")
            messagebox.showerror("Connection Error", str(e), parent=self)

    def _recv_loop(self):
        while self.running:
            try:
                msg_type, payload = recv_message(self.sock)
            except (ConnectionError, OSError):
                break
            if msg_type == MSG_JPEG:
                self.after(0, self._update_frame, payload)
            elif msg_type == MSG_JSON:
                # pong etc. - ignore
                pass

    def _update_frame(self, jpeg_data: bytes):
        if not self.running:
            return
        try:
            img = Image.open(io.BytesIO(jpeg_data))
            # Scale to fit canvas
            cw = self.canvas.winfo_width()
            ch = self.canvas.winfo_height()
            if cw > 1 and ch > 1:
                scale = min(cw / self.remote_width, ch / self.remote_height)
                new_w = int(self.remote_width * scale)
                new_h = int(self.remote_height * scale)
                img = img.resize((new_w, new_h), Image.BILINEAR)
            self._image_ref = ImageTk.PhotoImage(img)
            self.canvas.delete("all")
            self.canvas.create_image(
                self.canvas.winfo_width() // 2,
                self.canvas.winfo_height() // 2,
                image=self._image_ref,
            )
        except Exception as e:
            print(f"[Viewer] frame error: {e}")

    # ---------- Input forwarding ----------

    def _canvas_to_remote(self, x, y):
        """Convert canvas pixel coords to normalized 0..1 remote coords."""
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 2 or ch < 2:
            return 0.0, 0.0
        # Compute displayed image position (centered)
        scale = min(cw / self.remote_width, ch / self.remote_height)
        img_w = self.remote_width * scale
        img_h = self.remote_height * scale
        offset_x = (cw - img_w) / 2
        offset_y = (ch - img_h) / 2
        nx = (x - offset_x) / img_w if img_w > 0 else 0
        ny = (y - offset_y) / img_h if img_h > 0 else 0
        return max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny))

    def _send(self, obj):
        if self.sock and self.running:
            try:
                send_json(self.sock, obj)
            except OSError:
                pass

    def _on_mouse_move(self, e):
        nx, ny = self._canvas_to_remote(e.x, e.y)
        self._send({"type": "mouse_move", "x": nx, "y": ny})

    def _on_mouse_down(self, e):
        nx, ny = self._canvas_to_remote(e.x, e.y)
        self._send({"type": "mouse_down", "x": nx, "y": ny, "button": "left"})

    def _on_mouse_up(self, e):
        nx, ny = self._canvas_to_remote(e.x, e.y)
        self._send({"type": "mouse_up", "x": nx, "y": ny, "button": "left"})

    def _on_right_down(self, e):
        nx, ny = self._canvas_to_remote(e.x, e.y)
        self._send({"type": "mouse_down", "x": nx, "y": ny, "button": "right"})

    def _on_right_up(self, e):
        nx, ny = self._canvas_to_remote(e.x, e.y)
        self._send({"type": "mouse_up", "x": nx, "y": ny, "button": "right"})

    def _on_double_click(self, e):
        nx, ny = self._canvas_to_remote(e.x, e.y)
        self._send({"type": "mouse_double", "x": nx, "y": ny, "button": "left"})

    def _on_scroll(self, e, up=None):
        if up is None:
            dy = 1 if e.delta > 0 else -1
        else:
            dy = 1 if up else -1
        self._send({"type": "mouse_scroll", "dx": 0, "dy": dy})

    def _on_key_down(self, e):
        key = self._normalize_key(e.keysym)
        self._send({"type": "key_down", "key": key})

    def _on_key_up(self, e):
        key = self._normalize_key(e.keysym)
        self._send({"type": "key_up", "key": key})

    @staticmethod
    def _normalize_key(keysym: str) -> str:
        mapping = {
            "Control_L": "ctrl", "Control_R": "ctrl",
            "Alt_L": "alt", "Alt_R": "alt",
            "Shift_L": "shift", "Shift_R": "shift",
            "Super_L": "cmd", "Super_R": "cmd",
            "Return": "enter",
            "Escape": "esc",
            "space": "space",
            "BackSpace": "backspace",
            "Delete": "delete",
            "Up": "up", "Down": "down", "Left": "left", "Right": "right",
            "Home": "home", "End": "end",
            "Page_Up": "pageup", "Page_Down": "pagedown",
            "Caps_Lock": "capslock",
            "Tab": "tab",
        }
        if keysym in mapping:
            return mapping[keysym]
        if len(keysym) == 1:
            return keysym.lower()
        return keysym.lower()

    def _on_close(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.destroy()
