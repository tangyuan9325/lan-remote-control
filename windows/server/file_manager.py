"""
File manager for remote file browsing, upload, and download.
"""
import os
import json
import time
import threading
from datetime import datetime

class FileManager:
    CHUNK_SIZE = 64 * 1024  # 64KB per chunk

    def __init__(self, send_json_fn, send_chunk_fn):
        self._send_json = send_json_fn
        self._send_chunk = send_chunk_fn
        self._upload_state = None

    def list_directory(self, path: str):
        try:
            if not path or path == "/":
                if os.name == "nt":
                    drives = []
                    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                        d = f"{letter}:\\"
                        if os.path.exists(d):
                            drives.append({"name": d, "is_dir": True, "size": 0, "modified": ""})
                    self._send_json({"type": "file_list_response", "path": "/", "files": drives})
                    return
                path = os.path.expanduser("~")
            if not os.path.isdir(path):
                self._send_json({"type": "file_list_error", "path": path, "error": "Not a directory"})
                return
            files = []
            for name in sorted(os.listdir(path)):
                full = os.path.join(path, name)
                try:
                    stat = os.stat(full)
                    is_dir = os.path.isdir(full)
                    files.append({
                        "name": name,
                        "is_dir": is_dir,
                        "size": 0 if is_dir else stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    })
                except OSError:
                    continue
            self._send_json({"type": "file_list_response", "path": path, "files": files})
        except Exception as e:
            self._send_json({"type": "file_list_error", "path": path, "error": str(e)})

    def download_file(self, path: str):
        def _send():
            try:
                if not os.path.isfile(path):
                    self._send_json({"type": "file_download_error", "error": "File not found"})
                    return
                size = os.path.getsize(path)
                name = os.path.basename(path)
                self._send_json({"type": "file_download_start", "name": name, "size": size})
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(self.CHUNK_SIZE)
                        if not chunk:
                            break
                        self._send_chunk(chunk)
                self._send_json({"type": "file_download_complete", "name": name, "size": size})
            except Exception as e:
                self._send_json({"type": "file_download_error", "error": str(e)})
        threading.Thread(target=_send, daemon=True).start()

    def upload_start(self, path: str, name: str, size: int):
        try:
            if not os.path.isdir(path):
                os.makedirs(path, exist_ok=True)
            full_path = os.path.join(path, name)
            f = open(full_path, "wb")
            self._upload_state = {
                "path": full_path,
                "name": name,
                "size": size,
                "received": 0,
                "file": f,
            }
            self._send_json({"type": "file_upload_ready", "path": full_path})
        except Exception as e:
            self._send_json({"type": "file_upload_error", "error": str(e)})

    def upload_chunk(self, data: bytes):
        if self._upload_state and self._upload_state["file"]:
            self._upload_state["file"].write(data)
            self._upload_state["received"] += len(data)

    def upload_complete(self, name: str):
        if self._upload_state:
            self._upload_state["file"].close()
            received = self._upload_state["received"]
            full_path = self._upload_state["path"]
            self._send_json({"type": "file_upload_done", "path": full_path, "size": received})
            self._upload_state = None
