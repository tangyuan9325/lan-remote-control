"""
Protocol helpers for the PyQt5 viewer v1.2.
"""
import struct
import json
import socket

MSG_JSON = 0x01
MSG_JPEG = 0x02
MSG_FILE = 0x03
MSG_AUDIO = 0x04
HEADER_SIZE = 5

def pack_json(obj):
    payload = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return struct.pack(">BI", MSG_JSON, len(payload)) + payload

def pack_file(data):
    return struct.pack(">BI", MSG_FILE, len(data)) + data

def pack_audio(data):
    return struct.pack(">BI", MSG_AUDIO, len(data)) + data

def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf

def recv_message(sock):
    header = recv_exact(sock, HEADER_SIZE)
    msg_type, length = struct.unpack(">BI", header)
    payload = recv_exact(sock, length)
    return msg_type, payload

def send_json(sock, obj):
    sock.sendall(pack_json(obj))

def send_file(sock, data):
    sock.sendall(pack_file(data))

def send_audio(sock, data):
    sock.sendall(pack_audio(data))
