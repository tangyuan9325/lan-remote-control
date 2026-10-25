# LAN Remote Control

> 局域网远程控制套件（LAN Remote Control Suite）· v1.3.0
> 类似向日葵 / ToDesk 的纯内网远程控制方案，无需中转服务器。
> 本仓库由两个方向的项目合并而成，目录结构如下：

```
lan-remote-control/                 # 方向一：主项目（本目录）
├── go-server/                      #   Go 被控端引擎（含 Web 控制台）
├── android/                        #   Flutter 控制端 / 被控端 App
├── windows/                        #   Python 版 Windows 服务端 / 查看器
├── build/                          #   构建产物
├── docs/                           #   协议文档
├── scripts/                        #   打包脚本
└── legacy/remote_control/          # 方向二：早期实验项目（已安全重构为 rc14）
```

---

## 方向一：LAN Remote Control（主项目，v1.3.0）

支持 Windows 与 Android，纯内网通信、密码保护、设备自动发现。

### 特性

**v1.3.0 安全与功能加固（本次版本重点）**
- 🔒 **全链路鉴权**：TCP `hello` 密码校验（恒定时间比较防时序侧信道）；Web `/ws`、`/camera` 升级前强制校验密码
- 🌐 **Web 跨机代理**：浏览器可经 `?target=IP:PORT&remote_password=` 安全代理连接局域网内任意被控机（仅接受点分 IPv4，拒绝回环/链路本地/组播，防 SSRF 与 DNS rebinding）
- ⌨️ **输入注入落地**：Go 端新增 `inputinject` 包（Windows Win32 user32 实现，归一化坐标映射、虚拟键码表；其他平台安全 stub）
- 📷 **摄像头修复**：JPEG 按 SOI/EOI 滑动窗口切帧（修复跨读边界丢帧/坏帧）；引用计数式启停（修复单客户端退出停全局流）；FFmpeg 子进程可靠回收
- 🛡️ **DoS/泄漏防护**：连接数信号量（≤100）、首包 10s 握手超时、60s 读空闲超时、5s 写超时（慢客户端不再挂起推流 goroutine）、消息上限 64MB
- 📡 **UDP 发现加固**：原子运行标志消除竞态、速率限制清理、按 `runtime.GOOS` 上报系统
- 📱 **Android 被控端加固**：强制密码 + 连接数限制 + 握手超时 + 会话看门狗；O(n²) 解析缓冲重构为游标环形缓冲；下载 OOM 防护（64MB 消息 / 512MB 文件上限）；上传握手改事件驱动
- 🖼️ **静态资源内嵌**：Web UI 通过 `go:embed` 打进二进制，打包后不再有相对路径 404

**核心能力**
- 实时屏幕流（约 30fps JPEG，画质 10–100 可调，分辨率变更自动同步）
- 鼠标 / 键盘远程控制（move/down/up/click/double/scroll、按键、文本输入）
- 文件传输（列目录 / 上传 / 下载，分块协议）
- 语音通话（PCM 16bit 16kHz 双向音频帧）
- 设备自动发现（UDP 广播 :9000）
- ping/pong 心跳保活、优雅关闭（HTTP Shutdown + 信号处理）

### 平台支持

| 平台 | 被控端 | 控制端 |
|------|--------|--------|
| Windows | Go 引擎（推荐）/ Python server | Web 控制台 / PyQt5 查看器 |
| Android | Flutter 被控端（需设置密码） | Flutter 控制端 |
| Linux/macOS | Go 引擎可编译运行（输入注入为 stub） | Web 控制台 |

### 快速开始

**方式 A：Go 被控端（推荐）**
```bash
cd go-server
go build ./cmd/server -o lan-rc-server
./lan-rc-server --port 9001 --web 8080 --quality 50 --name "MyPC" --password "1234"
```
或直接使用预编译产物 `build/lan-remote-control-server.exe`（Windows）。

运行后：
- TCP 控制：端口 9001　·　Web UI：http://localhost:8080　·　UDP 发现：端口 9000
- 浏览器打开 `http://被控机IP:8080`，在控制台中填入被控机地址与密码即可远程观看与控制；支持扫描局域网设备、摄像头监视、画质调节。

**方式 B：Android**
安装 `android/remote_control_server` APK 作为被控端（首次启动请设置访问密码），用 `android/remote_control_app` 控制端自动搜索并连接。

**方式 C：Python Windows 服务端（过渡方案）**
```bash
cd windows/server && pip install -r requirements.txt && python main.py
cd windows/viewer_pyqt && pip install -r requirements.txt && python main.py
```

### 架构

```
go-server/                    # Go 被控端
├── cmd/server/main.go       # 入口：TCP 服务、握手、屏幕推流、优雅关闭
├── screen/capture.go        # 屏幕采集（线程安全、分辨率自适应）
├── camera/camera.go         # 摄像头（FFmpeg，SOI/EOI 切帧，引用计数）
├── inputinject/             # 输入注入（Win32 实现 + 跨平台 stub + 键码表）
├── protocol/protocol.go     # 分帧协议（5 字节头，64MB 上限）
├── discovery/discovery.go   # UDP 设备发现（限流、原子状态）
└── webui/                   # Web 控制端
    ├── server.go            # HTTP+WebSocket：鉴权、SSRF 防护、本机/代理双模式
    └── static/index.html    # Claude 风格深色玻璃拟态前端（go:embed 内嵌）

android/
├── remote_control_app/      # Android 控制端（Flutter，环形缓冲/OOM 防护）
└── remote_control_server/   # Android 被控端（Flutter，密码+限连+看门狗）

windows/                     # Python 版服务端 / Tkinter 查看器 / PyQt5 WebView 查看器
```

### 协议（详见 docs/protocol.md，v1.3）

- 头部：1 字节消息类型 + 4 字节大端长度；类型：`0x01` JSON / `0x02` JPEG / `0x03` 文件块 / `0x04` 音频
- 发现：UDP 9000，magic=`DISCOVER`
- 控制：TCP 9001；流程 `hello(password)` → `hello_ok(w,h)` → 持续 JPEG 帧 + 双向命令
- 安全：密码恒定时间比较、错误密码 `hello_fail`、ping/pong 保活

### 构建

```bash
# Go 被控端（Linux 开发机可交叉编译 Windows 版）
cd go-server && GOOS=windows GOARCH=amd64 go build -o ../build/lan-remote-control-server.exe ./cmd/server

# Android APK
scripts/build_android.sh

# Python 版 EXE（旧）
scripts/build_windows.bat
```

---

## 方向二：legacy/remote_control（早期实验项目 · 已完成安全重构）

基于 Python（Flask + PyQt5）的早期技术验证版。**当前推荐使用重构后的 `rc14` 包**（Claude 风格 UI、按需采集、多层安全防护）；历史单体文件（`main.py`、`remote_control_v1.x.py`）已全部加上弃用守卫——直接运行只会打印迁移提示并退出，杜绝误跑带漏洞的旧代码。

### 运行（v1.4 重构版）

```bash
cd legacy/remote_control
pip install -r requirements.txt
python -m rc14.main                      # GUI 控制面板（PyQt5，Claude 风格）
python -m rc14.main --headless \
    --port 5000 --password s3cret        # 无 GUI，仅服务器
python tests/test_rc14.py                # 自检（15 项测试）
build_rc14.bat                           # PyInstaller 一键打包（修复旧 packeg.bat/spec）
```

### 重构要点

- **UI**：Web 与 PyQt5 均改为 Claude 风格暖色排版
- **算力优化**：按需采集（无观看者时 worker 挂起）、帧差检测跳变推送、长边 ≤1920 分辨率钳制
- **安全**：访问密码、路径穿越拦截、上传名净化与大小校验、远程 CMD 默认禁用（`--enable-cmd` 显式开启）、XSS 消毒
- **打包**：新 `rc14.spec` 收集 flask/cv2/PyQt5 动态依赖与图标资源，修复旧 spec 入口路径错误导致的 ModuleNotFoundError / Qt 平台插件缺失

### 定位差异

| 维度 | 方向一：LAN Remote Control | 方向二：legacy/rc14 |
|------|---------------------------|--------------------|
| 语言/框架 | Go + Flutter + Web | Python（Flask + PyQt5） |
| 被控端 | Go 引擎（高性能） | rc14 重构版 Flask 服务器 |
| 状态 | 活跃开发（当前主线） | 存档 + 安全重构维护 |

---

## License

MIT
