"""
Wire protocol helpers for LAN Remote Control.

Message framing (5-byte header + payload):
  byte 0      : msg_type  (0x01=JSON, 0x02=JPEG)
  bytes 1..4  : length    (big-endian uint32)
  bytes 5..   : payload
"""

import struct
import json
import socket

MSG_JSON = 0x01
MSG_JPEG = 0x02

HEADER_SIZE = 5


def pack_json(obj: dict) -> bytes:
    """Serialize a dict into a framed JSON message."""
    payload = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    header = struct.pack(">BI", MSG_JSON, len(payload))
    return header + payload


def pack_jpeg(data: bytes) -> bytes:
    """Wrap raw JPEG bytes into a framed message."""
    header = struct.pack(">BI", MSG_JPEG, len(data))
    return header + data


def recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes from a socket (blocks)."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf


def recv_message(sock: socket.socket):
    """
    Read one framed message.
    Returns (msg_type, payload_bytes).
    """
    header = recv_exact(sock, HEADER_SIZE)
    msg_type, length = struct.unpack(">BI", header)
    payload = recv_exact(sock, length)
    return msg_type, payload


def send_json(sock: socket.socket, obj: dict) -> None:
    sock.sendall(pack_json(obj))


def send_jpeg(sock: socket.socket, data: bytes) -> None:
    sock.sendall(pack_jpeg(data))
