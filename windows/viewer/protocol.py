"""
Protocol helpers shared by the Windows viewer.
Same framing as server/protocol.py (kept independent for zero cross-imports).
"""

import struct
import json
import socket

MSG_JSON = 0x01
MSG_JPEG = 0x02
HEADER_SIZE = 5


def pack_json(obj: dict) -> bytes:
    payload = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return struct.pack(">BI", MSG_JSON, len(payload)) + payload


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf


def recv_message(sock: socket.socket):
    header = recv_exact(sock, HEADER_SIZE)
    msg_type, length = struct.unpack(">BI", header)
    payload = recv_exact(sock, length)
    return msg_type, payload


def send_json(sock: socket.socket, obj: dict) -> None:
    sock.sendall(pack_json(obj))
