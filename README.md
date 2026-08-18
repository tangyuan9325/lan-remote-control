# LAN Remote Control

一个开源的**内网远程控制**工具，仿向日葵 / ToDesk 的操作方式，支持 **Windows ↔ Windows** 和 **Android → Windows** 远程控制。所有通信在内网完成，无需中转服务器，低延迟。

> ⚠️ 本项目仅用于**合法的内网远程管理**（如家庭电脑、办公设备）。请勿用于未经授权的访问。

## 功能特性

- 🔍 **内网自动发现** — UDP 广播自动搜索同网段内的被控电脑
- 🖥️ **实时屏幕传输** — JPEG 帧压缩，30 FPS 目标帧率
- 🖱️ **完整鼠标控制** — 移动、左键/右键/中键、双击、滚轮
- ⌨️ **键盘输入** — 支持组合键、特殊键、文本输入
- 🔐 **密码保护** — 可选连接密码
- 📱 **Android 客户端** — Flutter 构建，触摸手势映射鼠标操作
- 🪟 **Windows 客户端** — Tkinter GUI，支持自动刷新设备列表
- 🌐 **纯内网通信** — 无需公网服务器，数据不出局域网

## 项目结构

```
RemoteControl/
├── windows/
│   ├── server/          # 被控端（运行在被控制的 Windows 电脑上）
│   │   ├── main.py
│   │   ├── screen_capture.py
│   │   ├── input_simulator.py
│   │   ├── discovery.py
│   │   ├── protocol.py
│   │   └── requirements.txt
│   └── viewer/          # 控制端（运行在控制用 Windows 电脑上）
│       ├── main.py
│       ├── viewer_window.py
│       ├── discovery.py
│       ├── protocol.py
│       └── requirements.txt
├── android/
│   └── remote_control_app/   # Flutter Android 控制端
│       ├── pubspec.yaml
│       ├── lib/
│       │   ├── main.dart
│       │   ├── discovery.dart
│       │   ├── connection.dart
│       │   ├── remote_view.dart
│       │   └── protocol.dart
│       └── android/
├── docs/
│   └── protocol.md      # 通信协议文档
├── scripts/
│   ├── build_windows.bat
│   └── build_android.sh
└── README.md
```

## 快速开始

### 1. 被控端（Windows，被控制的电脑）

```bash
cd windows/server
pip install -r requirements.txt
python main.py                       # 无密码
python main.py --password 1234       # 设置密码
python main.py --quality 80          # 调整画质 (10-100)
```

启动后程序会在后台监听：
- UDP 9000：设备发现
- TCP 9001：远程控制

### 2. 控制端 — Windows

```bash
cd windows/viewer
pip install -r requirements.txt
python main.py
```

- 点击 **Refresh** 搜索内网设备
- 双击设备即可连接（有密码会提示输入）
- 也可点击 **Connect by IP** 手动输入 IP

### 3. 控制端 — Android

```bash
cd android/remote_control_app
flutter pub get
flutter run            # 调试运行
flutter build apk      # 构建 APK
```

安装 APK 后：
- 确保手机和电脑在同一 WiFi
- 应用会自动搜索内网设备
- 点击设备连接
- **单击** = 鼠标左键，**双击** = 双击，**长按** = 右键，**拖动** = 鼠标移动，**键盘按钮** = 发送文本

## 通信协议

详见 [docs/protocol.md](docs/protocol.md)。

- **发现层**：UDP 广播 `DISCOVER` → 回复 JSON 设备信息
- **控制层**：TCP 长连接，5 字节消息头（1 字节类型 + 4 字节长度）+ payload
  - `0x01` JSON 控制消息（握手、输入事件）
  - `0x02` JPEG 屏幕帧

## 端口说明

| 端口 | 协议 | 用途 |
|------|------|------|
| 9000 | UDP | 设备发现（广播） |
| 9001 | TCP | 远程控制连接 |

如需修改端口，被控端用 `--port` 参数，控制端连接时指定对应端口。

## 构建发布版

### Windows EXE（使用 PyInstaller）

```bash
cd windows/server
pip install pyinstaller
pyinstaller --onefile --name RemoteControl-Server main.py

cd ../viewer
pyinstaller --onefile --name RemoteControl-Viewer main.py
```

### Android APK

```bash
cd android/remote_control_app
flutter build apk --release
# 产物: build/app/outputs/flutter-apk/app-release.apk
```

## 系统要求

- **被控端**：Windows 10/11，Python 3.9+
- **Windows 控制端**：Windows 10/11，Python 3.9+
- **Android 控制端**：Android 5.0+ (API 21+)，Flutter 3.10+

## 与向日葵 / ToDesk 的区别

| 特性 | 本项目 | 向日葵/ToDesk |
|------|--------|---------------|
| 内网直连 | ✅ | ✅（优先） |
| 公网穿透 | ❌ | ✅ |
| 文件传输 | ❌ | ✅ |
| 语音通话 | ❌ | ✅ |
| 开源 | ✅ | ❌ |
| 无账号体系 | ✅ | ❌ |
| 数据不出内网 | ✅ | 需配置 |

本项目定位为**轻量、开源、纯内网**的远程控制方案，适合家庭/办公内网场景。

## 安全提示

1. 建议始终设置连接密码
2. 仅在可信内网中使用
3. 被控端程序需要屏幕捕获和输入模拟权限
4. Windows Defender 可能会拦截输入模拟操作，如需正常使用请添加信任

## License

MIT License
