# LAN Remote Control Protocol (v1)

## Overview
A simple binary protocol for intranet remote control. All connections are TCP
except device discovery, which uses UDP broadcast.

## Ports
| Service | Port | Protocol |
|---------|------|----------|
| Discovery | 9000 | UDP broadcast |
| Control   | 9001 | TCP         |

## 1. Device Discovery (UDP)

### Client → Server (broadcast, 255.255.255.255:9000)
```
DISCOVER
```
Plain ASCII text, no trailing newline required.

### Server → Client (unicast reply)
```json
{
  "type": "discovery_response",
  "hostname": "DESKTOP-ABC123",
  "ip": "192.168.1.50",
  "port": 9001,
  "os": "Windows 11",
  "version": "1.0.0"
}
```
JSON encoded as UTF-8.

## 2. Control Connection (TCP)

### Message Framing
Every message on the TCP channel uses a 5-byte header:

| Offset | Size | Field      | Description                              |
|--------|------|------------|------------------------------------------|
| 0      | 1    | msg_type   | 0x01 = JSON, 0x02 = JPEG frame           |
| 1      | 4    | length     | Payload length in bytes (big-endian uint32) |
| 5      | N    | payload    | Message body                             |

### Handshake
1. Client connects to server:9001
2. Client sends JSON:
   ```json
   {"type":"hello","password":"optional-password"}
   ```
3. Server replies:
   - Success: `{"type":"hello_ok","width":1920,"height":1080}`
   - Failure: `{"type":"hello_fail","reason":"wrong_password"}`

### Screen Streaming
After handshake the server continuously sends JPEG frames:
- msg_type = 0x02
- payload = JPEG bytes

The client may request a quality change:
```json
{"type":"set_quality","quality":70}
```

### Input Events (Client → Server, JSON)

Mouse move (coordinates 0..65535, mapped to full screen):
```json
{"type":"mouse_move","x":0.5,"y":0.3}
```
x, y are normalized floats (0.0–1.0).

Mouse button:
```json
{"type":"mouse_down","button":"left","x":0.5,"y":0.3}
{"type":"mouse_up","button":"left","x":0.5,"y":0.3}
{"type":"mouse_click","button":"left","x":0.5,"y":0.3}
{"type":"mouse_double","button":"left","x":0.5,"y":0.3}
```
button: "left" | "right" | "middle"

Mouse scroll:
```json
{"type":"mouse_scroll","dx":0,"dy":-1}
```

Keyboard:
```json
{"type":"key_down","key":"a"}
{"type":"key_up","key":"a"}
{"type":"key_type","text":"hello"}
```

### Keep-alive
Either side may send:
```json
{"type":"ping"}
```
Reply:
```json
{"type":"pong"}
```
