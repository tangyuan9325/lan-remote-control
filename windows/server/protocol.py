"""
Wire protocol helpers for LAN Remote Control.
Message framing (5-byte header + payload):
  byte 0      : msg_type  (0x01=JSON, 0x02=JPEG, 0x03=File, 0x04=Audio)
  bytes 1..4  : length    (big-endian uint32)
  bytes 5..   : payload
"""
import struct
import json
import socket

MSG_JSON = 0x01
MSG_JPEG = 0x02
MSG_FILE = 0x03
MSG_AUDIO = 0x04
HEADER_SIZE = 5

def pack_json(obj: dict) -> bytes:
    payload = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    header = struct.pack(">BI", MSG_JSON, len(payload))
    return header + payload

def pack_jpeg(data: bytes) -> bytes:
    header = struct.pack(">BI", MSG_JPEG, len(data))
    return header + data

def pack_file(data: bytes) -> bytes:
    header = struct.pack(">BI", MSG_FILE, len(data))
    return header + data

def pack_audio(data: bytes) -> bytes:
    header = struct.pack(">BI", MSG_AUDIO, len(data))
    return header + data

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

def send_jpeg(sock: socket.socket, data: bytes) -> None:
    sock.sendall(pack_jpeg(data))

def send_file(sock: socket.socket, data: bytes) -> None:
    sock.sendall(pack_file(data))

def send_audio(sock: socket.socket, data: bytes) -> None:
    sock.sendall(pack_audio(data))
