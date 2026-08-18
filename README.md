# LAN Remote Control

一个开源的**内网远程控制**工具，仿向日葵 / ToDesk 的操作方式，支持 **Windows ↔ Windows** 和 **Android → Windows** 远程控制。所有通信在内网完成，无需中转服务器，低延迟。

> ⚠️ 本项目仅用于**合法的内网远程管理**（如家庭电脑、办公设备）。请勿用于未经授权的访问。

## 功能特性

- 🔍 **内网自动发现** — UDP 广播自动搜索同网段内的被控电脑
- 🖥️ **实时屏幕传输** — JPEG 帧压缩，30 FPS 目标帧率
- 🖱️ **完整鼠标控制** — 移动、左键/右键/中键、双击、滚轮
- ⌨️ **键盘输入** — 支持组合键、特殊键、文本输入
- 📁 **文件传输** (v1.2+) — 远程目录浏览、下载、上传
- 🎤 **语音通话** (v1.2+) — PCM 16kHz 实时双向语音
- 🔐 **密码保护** — 可选连接密码
- 📱 **Android 客户端** — Flutter 构建，触摸手势映射鼠标操作
- 🪟 **Windows 客户端** — PyQt5 WebView 现代 UI
- 🌐 **纯内网通信** — 无需公网服务器，数据不出局域网

## 项目结构

```
RemoteControl/
├── windows/
│   ├── server/          # 被控端
│   │   ├── main.py
│   │   ├── screen_capture.py
│   │   ├── input_simulator.py
│   │   ├── file_manager.py
│   │   ├── audio_handler.py
│   │   ├── discovery.py
│   │   ├── protocol.py
│   │   └── requirements.txt
│   ├── viewer_pyqt/     # PyQt5 WebView 控制端
│   │   ├── main.py
│   │   ├── bridge.py
│   │   ├── discovery.py
│   │   ├── protocol.py
│   │   ├── requirements.txt
│   │   └── web/
│   └── viewer/          # Tkinter 控制端（旧版）
├── android/
│   └── remote_control_app/
├── docs/
│   └── protocol.md
└── README.md
```

## 快速开始

### 1. 被控端（Windows）
```bash
cd windows/server
pip install -r requirements.txt
python main.py                       # 无密码
python main.py --password 1234       # 设置密码
```

### 2. 控制端 — Windows (PyQt5)
```bash
cd windows/viewer_pyqt
pip install -r requirements.txt
python main.py
```

### 3. 控制端 — Android
```bash
cd android/remote_control_app
flutter pub get
flutter build apk
```

## 通信协议
详见 [docs/protocol.md](docs/protocol.md)。

- `0x01` JSON 控制消息
- `0x02` JPEG 屏幕帧
- `0x03` 文件分块
- `0x04` PCM 音频帧

## 端口说明
| 端口 | 协议 | 用途 |
|------|------|------|
| 9000 | UDP | 设备发现 |
| 9001 | TCP | 远程控制 |

## 构建发布版
### Windows EXE
```bash
pip install pyinstaller
pyinstaller --onefile --name RemoteControl-Server main.py
```
### Android APK
```bash
flutter build apk --release
```

## License
MIT License
