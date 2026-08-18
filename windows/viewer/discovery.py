"""
UDP device discovery for the viewer (controller side).
Broadcasts DISCOVER and collects replies.
"""

import socket
import json
import threading
import time

DISCOVERY_PORT = 9000
DISCOVERY_MAGIC = b"DISCOVER"


def discover(timeout: float = 2.0) -> list:
    """
    Broadcast a DISCOVER packet and collect all replies.
    Returns a list of dicts: [{"hostname","ip","port","os","version",...}]
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.5)

    # Send multiple times to improve reliability
    for _ in range(3):
        try:
            sock.sendto(DISCOVERY_MAGIC, ("255.255.255.255", DISCOVERY_PORT))
        except OSError:
            pass
        time.sleep(0.1)

    devices = {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
        try:
            info = json.loads(data.decode("utf-8"))
            if info.get("type") == "discovery_response":
                # Use ip+port as key to deduplicate
                key = f"{info.get('ip')}:{info.get('port')}"
                devices[key] = info
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

    sock.close()
    return list(devices.values())


class DiscoveryThread(threading.Thread):
    """Background discovery that calls a callback with the device list."""

    def __init__(self, callback, interval: float = 5.0):
        super().__init__(daemon=True)
        self.callback = callback
        self.interval = interval
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                devices = discover(timeout=2.0)
                self.callback(devices)
            except Exception as e:
                print(f"[Discovery] error: {e}")
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
