"""
UDP LAN device discovery for the viewer.
"""

import socket
import json
import time

DISCOVERY_PORT = 9000
DISCOVERY_MAGIC = b"DISCOVER"


def discover(timeout: float = 2.0) -> list:
    """Broadcast DISCOVER and collect all replies."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.5)

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
                key = f"{info.get('ip')}:{info.get('port')}"
                devices[key] = info
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

    sock.close()
    return list(devices.values())
