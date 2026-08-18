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
