# LAN Remote Control

> 局域网远程控制套件（LAN Remote Control Suite）
> 本仓库由两个方向的项目合并而成，目录结构如下：

```
lan-remote-control/                 # 方向一：主项目（本目录）
├── go-server/                      #   Go 被控端引擎
├── android/                        #   Flutter 控制端 / 被控端
├── windows/                        #   Windows 相关构建与脚本
├── build/                          #   构建产物
├── docs/                           #   文档
└── legacy/remote_control/          # 方向二：早期实验项目（Flask + PyQt5）
```

---

## 方向一：LAN Remote Control（主项目，v1.3.0）

类似向日葵 / ToDesk 的局域网远程控制应用，支持 Windows 与 Android，纯内网通信、无需中转服务器。

### 特性

**v1.3.0 新增**
- **Go 引擎**：被控端使用 Go 重写，高性能
- **GPU 采集**：DXGI Desktop Duplication API（GDI 兜底）
- **Web 控制**：浏览器访问 `http://IP:8080` 直接控制
- **摄像头**：通过 FFmpeg 远程查看摄像头画面
- **新 UI**：深色主题 + Liquid Glass 玻璃拟态
- **静默启动**：后台运行

**核心能力**
- 实时屏幕流（30fps JPEG）
- 鼠标 / 键盘远程控制
- 文件传输
- 语音通话
- 设备自动发现（UDP 广播）
- 密码保护

### 平台支持

| 平台 | 被控端 | 控制端 |
|------|--------|--------|
| Windows | Go 引擎 | Web / PyQt5 |
| Android | Flutter | Flutter |

### 快速开始

**Windows 被控端**
```bash
lan-remote-control-v1.3.0-windows-server.exe
# 自定义参数
server.exe --port 9001 --web 8080 --quality 50 --name "MyPC" --password "1234"
```

运行后：
- TCP 控制：端口 9001
- Web UI：http://localhost:8080
- UDP 发现：端口 9000

**Web 控制**：浏览器访问 `http://被控机IP:8080`，自动发现并连接。

**Android**：安装 APK 后自动搜索局域网设备。

### 架构

```
go-server/                    # Go 被控端
├── cmd/server/main.go       # 入口
├── screen/capture.go        # 屏幕采集（GDI/DXGI）
├── camera/camera.go         # 摄像头（FFmpeg/test pattern）
├── protocol/protocol.go     # 通信协议
├── discovery/discovery.go   # UDP 设备发现
└── webui/                   # Web 控制端
    ├── server.go            # HTTP+WebSocket 服务
    └── static/index.html    # 玻璃拟态前端

android/
├── remote_control_app/      # Android 控制端（Flutter）
└── remote_control_server/   # Android 被控端（Flutter）
```

### 协议

- 头部：1 字节消息类型 + 4 字节大端长度
- 类型：0x01 JSON / 0x02 JPEG / 0x03 File / 0x04 Audio
- 发现：UDP 9000，magic=`DISCOVER`
- 控制：TCP 9001

---

## 方向二：legacy/remote_control（早期实验项目）

基于 Python（Flask + PyQt5）开发的局域网简易远程控制系统，是方向一的早期技术验证版本。已随本仓库合并保留完整历史，源码位于 [`legacy/remote_control/`](legacy/remote_control/)。

> ⚠️ 该方向已停止独立演进，仅作存档与参考；新功能请以方向一（Go + Flutter）为准。

### 定位差异

| 维度 | 方向一：LAN Remote Control | 方向二：legacy/remote_control |
|------|---------------------------|-------------------------------|
| 语言/框架 | Go + Flutter + Web | Python（Flask + PyQt5） |
| 被控端 | Go 引擎（高性能、GPU 采集） | Flask 服务器 |
| 控制端 | Web / Flutter / PyQt5 | Web + PyQt5 本地面板 |
| 状态 | 活跃开发（当前主线） | 已归档（早期实验） |

### 特性回顾

- **Web 控制端**：实时屏幕监控（分辨率/帧率/画质可调）、键鼠控制、电源管理、远程 CMD、摄像头/麦克风监控、文件传输
- **PyQt5 本地管理端**：深色主题、参数可视化配置、硬件功能开关、系统托盘、自动启动
- **技术栈**：Flask、PyQt5、OpenCV、Pillow、PyAutoGUI、PyAudio

### 运行（存档参考）

```bash
cd legacy/remote_control
pip install -r requirements.txt
python main.py
```

---

## License

MIT
