#!/usr/bin/env python3
"""
LAN Remote Control - Server (controlled side) v1.2
Features: screen streaming, input simulation, file transfer, voice chat.
"""
import argparse
import json
import socket
import threading
import time
import sys
from protocol import (
    MSG_JSON, MSG_JPEG, MSG_FILE, MSG_AUDIO,
    recv_message, send_json, send_jpeg, send_file, send_audio,
)
from screen_capture import ScreenCapture
from input_simulator import InputSimulator
from discovery import DiscoveryServer
from file_manager import FileManager
from audio_handler import AudioHandler

CONTROL_PORT = 9001
FPS_TARGET = 30

class ClientSession:
    def __init__(self, conn, addr, capture, password=None):
        self.conn = conn
        self.addr = addr
        self.capture = capture
        self.password = password
        self.input_sim = None
        self.file_mgr = None
        self.audio = None
        self.authenticated = False
        self.running = False
        self._stream_thread = None

    def start(self):
        self.running = True
        try:
            self._handle()
        except (ConnectionError, OSError) as e:
            print(f"[Session {self.addr}] disconnected: {e}")
        finally:
            self.running = False
            if self.audio:
                self.audio.stop()
            self.conn.close()

    def _handle(self):
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
        self.file_mgr = FileManager(
            send_json_fn=lambda obj: send_json(self.conn, obj),
            send_chunk_fn=lambda data: send_file(self.conn, data),
        )
        self.audio = AudioHandler(
            send_audio_fn=lambda data: send_audio(self.conn, data),
        )
        self._stream_thread = threading.Thread(target=self._stream_loop, daemon=True)
        self._stream_thread.start()
        while self.running:
            msg_type, payload = recv_message(self.conn)
            if msg_type == MSG_JSON:
                self._dispatch(json.loads(payload.decode("utf-8")))
            elif msg_type == MSG_FILE:
                if self.file_mgr:
                    self.file_mgr.upload_chunk(payload)
            elif msg_type == MSG_AUDIO:
                if self.audio:
                    self.audio.play_audio(payload)

    def _dispatch(self, msg):
        t = msg.get("type")
        if t == "ping":
            send_json(self.conn, {"type": "pong"})
        elif t == "set_quality":
            self.capture.set_quality(msg.get("quality", 70))
        elif t == "file_list":
            if self.file_mgr: self.file_mgr.list_directory(msg.get("path", ""))
        elif t == "file_download":
            if self.file_mgr: self.file_mgr.download_file(msg.get("path", ""))
        elif t == "file_upload_start":
            if self.file_mgr:
                self.file_mgr.upload_start(msg.get("path", "."), msg.get("name", "upload"), msg.get("size", 0))
        elif t == "file_upload_complete":
            if self.file_mgr: self.file_mgr.upload_complete(msg.get("name", ""))
        elif t == "voice_start":
            if self.audio and self.audio.available:
                self.audio.start_recording()
                send_json(self.conn, {"type": "voice_ready"})
            else:
                send_json(self.conn, {"type": "voice_error", "error": "Audio unavailable"})
        elif t == "voice_stop":
            if self.audio: self.audio.stop_recording()
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
    parser = argparse.ArgumentParser(description="LAN Remote Control Server v1.2")
    parser.add_argument("--port", type=int, default=CONTROL_PORT)
    parser.add_argument("--password", type=str, default=None)
    parser.add_argument("--quality", type=int, default=70)
    parser.add_argument("--hostname", type=str, default=None)
    args = parser.parse_args()
    capture = ScreenCapture(quality=args.quality)
    discovery = DiscoveryServer(control_port=args.port, hostname=args.hostname, password=args.password)
    discovery.start()
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("0.0.0.0", args.port))
    server_sock.listen(5)
    print(f"[Server] v1.2 Ready on TCP 0.0.0.0:{args.port}")
    try:
        while True:
            conn, addr = server_sock.accept()
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
