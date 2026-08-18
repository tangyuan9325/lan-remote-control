#!/usr/bin/env python3
"""
LAN Remote Control - Server (controlled side)
Runs on the Windows PC to be controlled.

Features:
  - UDP discovery: announces itself on the LAN
  - TCP control server: streams screen + receives input events
  - Password protection (optional)

Usage:
  python main.py                          # no password
  python main.py --password 1234          # with password
  python main.py --port 9001 --quality 70
"""

import argparse
import json
import socket
import struct
import threading
import time
import sys

from protocol import (
    MSG_JSON, MSG_JPEG, recv_message, send_json, send_jpeg,
)
from screen_capture import ScreenCapture
from input_simulator import InputSimulator
from discovery import DiscoveryServer

CONTROL_PORT = 9001
FPS_TARGET = 30


class ClientSession:
    """Handles one connected viewer client."""

    def __init__(self, conn: socket.socket, addr, capture: ScreenCapture,
                 password: str = None):
        self.conn = conn
        self.addr = addr
        self.capture = capture
        self.password = password
        self.input_sim = None
        self.authenticated = False
        self.running = False
        self._stream_thread = None

    def start(self):
        self.running = True
        # Input receiver runs on the main session thread
        try:
            self._handle()
        except (ConnectionError, OSError) as e:
            print(f"[Session {self.addr}] disconnected: {e}")
        finally:
            self.running = False
            self.conn.close()
            print(f"[Session {self.addr}] closed")

    def _handle(self):
        # --- Handshake ---
        msg_type, payload = recv_message(self.conn)
        if msg_type != MSG_JSON:
            return
        msg = json.loads(payload.decode("utf-8"))
        if msg.get("type") != "hello":
            return

        if self.password and msg.get("password") != self.password:
            send_json(self.conn, {"type": "hello_fail", "reason": "wrong_password"})
            return

        w, h = self.capture.size
        send_json(self.conn, {"type": "hello_ok", "width": w, "height": h})
        self.authenticated = True
        self.input_sim = InputSimulator(w, h)
        print(f"[Session {self.addr}] authenticated, screen {w}x{h}")

        # Start screen streaming thread
        self._stream_thread = threading.Thread(target=self._stream_loop, daemon=True)
        self._stream_thread.start()

        # Receive input events
        while self.running:
            msg_type, payload = recv_message(self.conn)
            if msg_type != MSG_JSON:
                continue
            self._dispatch(json.loads(payload.decode("utf-8")))

    def _dispatch(self, msg: dict):
        t = msg.get("type")
        if t == "ping":
            send_json(self.conn, {"type": "pong"})
        elif t == "set_quality":
            self.capture.set_quality(msg.get("quality", 70))
        elif not self.input_sim:
            return
        elif t == "mouse_move":
            self.input_sim.mouse_move(msg["x"], msg["y"])
        elif t == "mouse_down":
            self.input_sim.mouse_down(msg["x"], msg["y"], msg.get("button", "left"))
        elif t == "mouse_up":
            self.input_sim.mouse_up(msg["x"], msg["y"], msg.get("button", "left"))
        elif t == "mouse_click":
            self.input_sim.mouse_click(msg["x"], msg["y"], msg.get("button", "left"))
        elif t == "mouse_double":
            self.input_sim.mouse_double(msg["x"], msg["y"], msg.get("button", "left"))
        elif t == "mouse_scroll":
            self.input_sim.mouse_scroll(msg.get("dx", 0), msg.get("dy", 0))
        elif t == "key_down":
            self.input_sim.key_down(msg.get("key", ""))
        elif t == "key_up":
            self.input_sim.key_up(msg.get("key", ""))
        elif t == "key_type":
            self.input_sim.key_type(msg.get("text", ""))

    def _stream_loop(self):
        interval = 1.0 / FPS_TARGET
        while self.running:
            t0 = time.time()
            try:
                jpeg = self.capture.capture_jpeg()
                send_jpeg(self.conn, jpeg)
            except (ConnectionError, OSError):
                self.running = False
                break
            elapsed = time.time() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)


def main():
    parser = argparse.ArgumentParser(description="LAN Remote Control Server")
    parser.add_argument("--port", type=int, default=CONTROL_PORT,
                        help=f"TCP control port (default {CONTROL_PORT})")
    parser.add_argument("--password", type=str, default=None,
                        help="Optional connection password")
    parser.add_argument("--quality", type=int, default=70,
                        help="JPEG quality 10-100 (default 70)")
    parser.add_argument("--hostname", type=str, default=None,
                        help="Override displayed hostname")
    args = parser.parse_args()

    capture = ScreenCapture(quality=args.quality)
    w, h = capture.size
    print(f"[Server] Screen: {w}x{h}, quality={args.quality}")

    # Start UDP discovery
    discovery = DiscoveryServer(
        control_port=args.port,
        hostname=args.hostname,
        password=args.password,
    )
    discovery.start()

    # Start TCP server
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("0.0.0.0", args.port))
    server_sock.listen(5)
    print(f"[Server] TCP listening on 0.0.0.0:{args.port}")
    if args.password:
        print("[Server] Password protection ENABLED")
    print("[Server] Ready. Press Ctrl+C to stop.")

    try:
        while True:
            conn, addr = server_sock.accept()
            print(f"[Server] New connection from {addr}")
            session = ClientSession(conn, addr, capture, args.password)
            threading.Thread(target=session.start, daemon=True).start()
    except KeyboardInterrupt:
        print("\n[Server] Shutting down...")
    finally:
        server_sock.close()
        discovery.stop()
        capture.close()


if __name__ == "__main__":
    main()
