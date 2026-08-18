# LAN Remote Control Protocol (v1.2)

## Ports
| Service | Port | Protocol |
|---------|------|----------|
| Discovery | 9000 | UDP broadcast |
| Control   | 9001 | TCP         |

## Message Framing (TCP)
5-byte header + payload:
| Offset | Size | Field      | Description                              |
|--------|------|------------|------------------------------------------|
| 0      | 1    | msg_type   | 0x01=JSON, 0x02=JPEG, 0x03=FileChunk, 0x04=Audio |
| 1      | 4    | length     | big-endian uint32                        |
| 5      | N    | payload    | body                                     |

## 1. Device Discovery (UDP 9000)
Client broadcast: `DISCOVER`
Server reply: JSON `{"type":"discovery_response","hostname":...,"ip":...,"port":9001,"os":...,"version":"1.2.0","password_required":bool}`

## 2. Handshake
Client → `{"type":"hello","password":"..."}`
Server → `{"type":"hello_ok","width":1920,"height":1080}` or `{"type":"hello_fail","reason":"..."}`

## 3. Screen Streaming
Server sends 0x02 JPEG frames continuously.
Client may send `{"type":"set_quality","quality":70}`.

## 4. Input Events (Client → Server, JSON)
- `{"type":"mouse_move","x":0.5,"y":0.3}` (normalized 0..1)
- `{"type":"mouse_down/up/click/double","button":"left|right|middle","x":..,"y":..}`
- `{"type":"mouse_scroll","dx":0,"dy":-1}`
- `{"type":"key_down/up","key":"a"}`
- `{"type":"key_type","text":"hello"}`

## 5. File Transfer (v1.2+)
### List directory
Client → `{"type":"file_list","path":"C:/Users"}`
Server → `{"type":"file_list_response","path":"C:/Users","files":[{"name":"Public","is_dir":true,"size":0,"modified":"2024-01-01"}]}`
Error → `{"type":"file_list_error","path":"...","error":"..."}`

### Download file (Server → Client)
1. Client → `{"type":"file_download","path":"C:/test.txt"}`
2. Server → `{"type":"file_download_start","name":"test.txt","size":12345}`
3. Server → multiple 0x03 FileChunk frames (raw bytes)
4. Server → `{"type":"file_download_complete","name":"test.txt","size":12345}`
Error → `{"type":"file_download_error","error":"..."}`

### Upload file (Client → Server)
1. Client → `{"type":"file_upload_start","path":"C:/upload/","name":"test.txt","size":12345}`
2. Server → `{"type":"file_upload_ready","path":"C:/upload/test.txt"}`
3. Client → multiple 0x03 FileChunk frames (raw bytes)
4. Client → `{"type":"file_upload_complete","name":"test.txt"}`
Server → `{"type":"file_upload_done","path":"C:/upload/test.txt","size":12345}`
Error → `{"type":"file_upload_error","error":"..."}`

### FileChunk (0x03)
Raw binary file data. Receiver reassembles chunks in order.

## 6. Voice Chat (v1.2+)
Audio format: PCM 16-bit signed, 16000 Hz, mono.

### Start voice
Either side → `{"type":"voice_start"}`
Other side → `{"type":"voice_ready"}`

### Audio frames
Both sides send 0x04 frames containing raw PCM data.

### Stop voice
Either side → `{"type":"voice_stop"}`

## 7. Keep-alive
`{"type":"ping"}` → `{"type":"pong"}`
