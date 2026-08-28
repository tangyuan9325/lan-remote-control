"""
UDP device discovery for the server.
"""
import socket
import json
import threading
import platform

DISCOVERY_PORT = 9000
DISCOVERY_MAGIC = b"DISCOVER"

class DiscoveryServer:
    def __init__(self, control_port=9001, hostname=None, password=None):
        self.control_port = control_port
        self.hostname = hostname or socket.gethostname()
        self.password = password
        self._sock = None
        self._thread = None
        self._running = False
        # 速率限制：每个 IP 每秒最多响应 5 次发现请求，防止 DoS
        self._rate_limit = {}
        self._rate_limit_lock = threading.Lock()

    def _check_rate_limit(self, ip: str) -> bool:
        """检查 IP 是否在速率限制内，返回 True 表示允许响应"""
        import time
        with self._rate_limit_lock:
            now = time.time()
            # 清理过期记录（超过 1 秒的）
            self._rate_limit = {k: v for k, v in self._rate_limit.items() if now - v < 1.0}
            count = self._rate_limit.get(ip, 0)
            if count >= 5:
                return False
            self._rate_limit[ip] = count + 1
            return True

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("0.0.0.0", DISCOVERY_PORT))
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self._running:
            try:
                data, addr = self._sock.recvfrom(1024)
            except OSError:
                break
            if data.strip() == DISCOVERY_MAGIC:
                # 安全检查：速率限制，防止 DoS
                if not self._check_rate_limit(addr[0]):
                    continue  # 超过速率限制，忽略请求
                local_ip = self._get_local_ip_for(addr[0])
                reply = {
                    "type": "discovery_response",
                    "hostname": self.hostname,
                    "ip": local_ip,
                    "port": self.control_port,
                    "os": f"{platform.system()} {platform.release()}",
                    "version": "1.2.0",
                    "password_required": bool(self.password),
                }
                self._sock.sendto(json.dumps(reply).encode("utf-8"), addr)

    @staticmethod
    def _get_local_ip_for(target_ip):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((target_ip, DISCOVERY_PORT))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return "0.0.0.0"

    def stop(self):
        self._running = False
        if self._sock:
            self._sock.close()
