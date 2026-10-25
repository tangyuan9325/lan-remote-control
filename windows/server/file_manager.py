"""
File manager for remote file browsing, upload, and download.
Security: all paths are sandboxed within BASE_DIR to prevent path traversal.
"""
import os
import json
import time
import threading
from datetime import datetime

# 沙箱根目录：用户主目录下的 LANRemoteControl 文件夹
# 所有文件操作都限制在此目录内，防止路径遍历攻击
BASE_DIR = os.path.realpath(os.path.join(os.path.expanduser("~"), "LANRemoteControl"))
os.makedirs(BASE_DIR, exist_ok=True)


def _safe_path(user_path: str) -> str:
    """将用户输入路径规范化并验证是否在沙箱目录内。
    返回规范化后的绝对路径，越界则抛出 ValueError。
    """
    if not user_path or user_path == "/":
        return BASE_DIR
    # 拒绝空字节
    if "\x00" in user_path:
        raise ValueError("Invalid path: null byte detected")
    # 如果是相对路径，拼接到 BASE_DIR；如果是绝对路径，直接使用但需验证
    if os.path.isabs(user_path):
        candidate = os.path.realpath(user_path)
    else:
        candidate = os.path.realpath(os.path.join(BASE_DIR, user_path))
    # 验证路径在沙箱内
    if not (candidate == BASE_DIR or candidate.startswith(BASE_DIR + os.sep)):
        raise ValueError(f"Access denied: path outside sandbox: {user_path}")
    return candidate

class FileManager:
    CHUNK_SIZE = 64 * 1024  # 64KB per chunk

    def __init__(self, send_json_fn, send_chunk_fn):
        self._send_json = send_json_fn
        self._send_chunk = send_chunk_fn
        self._upload_state = None

    def list_directory(self, path: str):
        try:
            # 安全检查：路径沙箱验证
            try:
                safe_dir = _safe_path(path)
            except ValueError as e:
                self._send_json({"type": "file_list_error", "path": path, "error": str(e)})
                return
            if not os.path.isdir(safe_dir):
                self._send_json({"type": "file_list_error", "path": path, "error": "Not a directory"})
                return
            files = []
            for name in sorted(os.listdir(safe_dir)):
                full = os.path.join(safe_dir, name)
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
            self._send_json({"type": "file_list_response", "path": safe_dir, "files": files})
        except Exception as e:
            self._send_json({"type": "file_list_error", "path": path, "error": str(e)})

    def download_file(self, path: str):
        def _send():
            try:
                # 安全检查：路径沙箱验证
                try:
                    safe_file = _safe_path(path)
                except ValueError as e:
                    self._send_json({"type": "file_download_error", "error": str(e)})
                    return
                if not os.path.isfile(safe_file):
                    self._send_json({"type": "file_download_error", "error": "File not found"})
                    return
                size = os.path.getsize(safe_file)
                name = os.path.basename(safe_file)
                self._send_json({"type": "file_download_start", "name": name, "size": size})
                with open(safe_file, "rb") as f:
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
            # 安全检查：路径沙箱验证
            try:
                safe_dir = _safe_path(path)
            except ValueError as e:
                self._send_json({"type": "file_upload_error", "error": str(e)})
                return
            # 安全检查：文件名只取 basename，防止路径遍历
            safe_name = os.path.basename(name)
            if not safe_name:
                self._send_json({"type": "file_upload_error", "error": "Invalid filename"})
                return
            # 安全检查：拒绝危险字符（空字节、路径分隔符等）
            if any(c in safe_name for c in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']):
                self._send_json({"type": "file_upload_error", "error": "Invalid characters in filename"})
                return
            # 安全检查：限制文件大小（最大 1GB）
            MAX_UPLOAD_SIZE = 1024 * 1024 * 1024
            if size > MAX_UPLOAD_SIZE:
                self._send_json({"type": "file_upload_error", "error": f"File too large (max {MAX_UPLOAD_SIZE} bytes)"})
                return
            if not os.path.isdir(safe_dir):
                os.makedirs(safe_dir, exist_ok=True)
            full_path = os.path.join(safe_dir, safe_name)
            f = open(full_path, "wb")
            self._upload_state = {
                "path": full_path,
                "name": safe_name,
                "size": size,
                "received": 0,
                "file": f,
            }
            self._send_json({"type": "file_upload_ready", "path": full_path})
        except Exception as e:
            self._send_json({"type": "file_upload_error", "error": str(e)})

    def upload_chunk(self, data: bytes):
        if self._upload_state and self._upload_state["file"]:
            # Enforce the declared size so a malicious/buggy client cannot
            # write past the announced length into the sandbox.
            expected = self._upload_state["size"]
            received = self._upload_state["received"]
            if received + len(data) > expected:
                allowed = max(0, expected - received)
                if allowed:
                    self._upload_state["file"].write(data[:allowed])
                    self._upload_state["received"] += allowed
                self._abort_upload(error="Upload exceeded declared size")
                return
            self._upload_state["file"].write(data)
            self._upload_state["received"] += len(data)

    def _abort_upload(self, error: str = "Upload aborted"):
        """Close and remove a partially written upload."""
        state = self._upload_state
        self._upload_state = None
        if not state:
            return
        try:
            if state["file"]:
                state["file"].close()
        except OSError:
            pass
        try:
            os.remove(state["path"])
        except OSError:
            pass
        self._send_json({"type": "file_upload_error", "error": error})

    def upload_complete(self, name: str):
        if self._upload_state:
            received = self._upload_state["received"]
            expected = self._upload_state["size"]
            full_path = self._upload_state["path"]
            safe_name = self._upload_state["name"]
            # Re-check the final path (defense in depth against symlink swaps).
            real = os.path.realpath(full_path)
            if not (real == BASE_DIR or real.startswith(BASE_DIR + os.sep)):
                self._abort_upload(error="Access denied: path outside sandbox")
                return
            if received != expected:
                self._abort_upload(
                    error=f"Size mismatch: expected {expected}, got {received}")
                return
            self._upload_state["file"].close()
            self._upload_state = None
            self._send_json({"type": "file_upload_done", "path": full_path, "size": received, "name": safe_name})
