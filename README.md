# LAN Remote Control v1.3.0

A LAN remote control application similar to Sunlogin/ToDesk, supporting Windows and Android.

## Features

### v1.3.0 New
- **Go Engine**: Rewritten controlled side in Go for high performance
- **GPU Capture**: DXGI Desktop Duplication API (GDI fallback)
- **Web Control**: Browser-based control at http://IP:8080
- **Camera**: Remote camera viewing via FFmpeg
- **New UI**: Dark theme + Liquid Glass glassmorphism
- **Silent Start**: Background operation

### Core
- Real-time screen streaming (30fps JPEG)
- Mouse/keyboard remote control
- File transfer
- Voice chat
- Auto device discovery (UDP broadcast)
- Password protection

## Platform Support

| Platform | Controlled | Controller |
|----------|-----------|------------|
| Windows | Go engine | Web / PyQt5 |
| Android | Flutter | Flutter |

## Quick Start

### Windows Server
```bash
lan-remote-control-v1.3.0-windows-server.exe
# Custom options
server.exe --port 9001 --web 8080 --quality 50 --name "MyPC" --password "1234"
```

After running:
- TCP control: port 9001
- Web UI: http://localhost:8080
- UDP discovery: port 9000

### Web Control
Open browser at http://CONTROLLED_PC_IP:8080, auto-discover and connect.

### Android
Install APKs, auto-search LAN devices.

## Architecture

```
go-server/                    # Go controlled side
├── cmd/server/main.go       # Entry point
├── screen/capture.go        # Screen capture (GDI/DXGI)
├── camera/camera.go         # Camera (FFmpeg/test pattern)
├── protocol/protocol.go     # Wire protocol
├── discovery/discovery.go   # UDP device discovery
└── webui/                   # Web control UI
    ├── server.go            # HTTP+WebSocket server
    └── static/index.html    # Glassmorphism frontend

android/
├── remote_control_app/      # Android controller (Flutter)
└── remote_control_server/   # Android controlled side (Flutter)
```

## Protocol

- Header: 1 byte msg type + 4 bytes big-endian length
- Types: 0x01 JSON / 0x02 JPEG / 0x03 File / 0x04 Audio
- Discovery: UDP 9000, magic="DISCOVER"
- Control: TCP 9001

## License

MIT
