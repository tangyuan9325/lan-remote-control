# ADB 远程控制台（Go + Android Agent）

> v1.1.0 · 通过 ADB 远程控制多台 Android 设备（USB / 无线调试均可）

一套「电脑在看、在点你手机」的工具，分两部分：

- **Windows 客户端**：纯 Go 编写的本地服务 + 浏览器控制台（无 cgo、无运行时依赖），双击即用
- **Android Agent（可选）**：低延迟 H.264 视频串流 + 无障碍手势注入（比 shell 输入更快、支持手势）

```
┌─────────────── Windows 客户端 (client.exe) ───────────────┐
│  本地 HTTP 服务 127.0.0.1:8500（仅回环 + Origin 校验）      │
│  ├─ 浏览器控制台（内嵌 HTML/JS，自动打开）                    │
│  ├─ ADB 层：设备发现 / 连接 / 配对 / 截图 / 录屏 / 输入       │
│  ├─ ffmpeg 层：H.264 → MJPEG 直出（零拷贝转发给浏览器）       │
│  └─ Agent 层：连接手机端串流/手势服务                        │
└──────────────┬────────────────────────────┬────────────────┘
        USB / 无线调试 (adb)         端口转发 (adb forward)
┌──────────────▼───────────────────┐ ┌──▼────────────────────────────┐
│      Android 设备（裸机，无需装APK）│ │  Android 设备 + Agent (APK)    │
│  screenrecord + adb input        │ │  8585 串流 / 8586 手势（回环）  │
└──────────────────────────────────┘ └───────────────────────────────┘
```

---

## v1.1.0 本次更新

这一版是对代码的逐行审查与重构，详细问题清单见 **[docs/CODE_REVIEW.md](docs/CODE_REVIEW.md)**（含每个缺陷的位置、成因与修复）。

### 安全加固

| 问题 | 影响 | 处理 |
|---|---|---|
| Android 清单缺 `INTERNET` 权限 | **Agent 两个端口根本绑不上**，串流/手势实际不可用 | 补权限并验证到 APK 二进制清单 |
| WebSocket `CheckOrigin` 恒为 `true` | 任意恶意网页可跨站接管手机（CSWSH）：读屏 + 注入点击 | 校验 Origin，跨站一律拒绝 |
| 未校验 Host | DNS rebinding 可绕过「只监听 127.0.0.1」 | Host 必须为 `127.0.0.1`/`localhost`/`::1` |
| Agent 端口绑定 `0.0.0.0` 且无鉴权 | 同一 Wi-Fi 下任何人可看屏、可点手机 | 改绑回环，必须经 `adb forward`（受 adb 授权保护） |
| 输入参数未校验 | 超大 `duration` 让 adb 挂满 30 秒；负坐标直接拼接 | 坐标按屏幕尺寸校验，`duration` 钳制 20~3000ms |
| 无请求体/消息上限、无心跳 | 本地 DoS、死连接不回收 | 64KB 上限、20s Ping、读写超时 |

> 实测：伪造 Host → 403；跨站 POST → 403；跨站 WebSocket 升级 → 拒绝；同源访问 → 200。

### 正确性修复

- **每帧像素错位**：`ReadFrame` 用同一个下标同时索引 3 字节/像素的 BGR 缓冲和 4 字节/像素的 RGBA 缓冲，导致整帧对角偏色重排。该函数已随解码链路重构删除。
- **多标签页互相踢掉**：每个 Session 只保存一个广播回调，开第二个标签页会覆盖第一个，关任一个又整体置空 → 改为订阅者集合。
- **数据竞态 5 处**：`videoMode`/`inputMode`/`info`/`ctrl`/`devices` 无锁跨协程访问 → 加锁，`go test -race` 全绿。
- **Agent 连接永不重建**：控制连接失效后被永久复用，每次点击先失败再回落 shell → 失活即重建。
- **串流服务只 `accept()` 一次**：客户端一断开服务就自杀，用户被迫反复授权录屏 → 改 accept 循环 + `MediaProjection.Callback`。
- **手势服务串行处理**：旧连接不关闭会饿死新连接 → 每连接独立线程 + 读超时。
- **退出不回收子进程**：关掉客户端后手机仍在录屏、电脑残留 ffmpeg → 捕获信号优雅退出。
- 另修 10 处小问题（配置非原子写、死代码、JSON 应答被特殊字符破坏、`dispatchGesture` 返回值被忽略等）。

### 性能与算法优化

**① 解码链路改为 MJPEG 直通**（收益最大）

```
旧：H.264 → ffmpeg 解码 → bgr24 rawvideo ──管道──→ Go 逐像素 BGR→RGBA → Go JPEG 编码 → 浏览器
新：H.264 → ffmpeg 解码 + MJPEG 编码（SIMD 多线程）──管道──→ Go 只做 SOI/EOI 切帧 → 浏览器
```

| 指标（1080p/帧） | 旧 | 新 |
|---|---|---|
| 管道数据量 | ~6.2 MB | ~0.1 MB |
| Go 侧全帧遍历 | 2 次 | 0 次 |
| 分辨率变化 | 帧长错位，必须重启 | 自适应 |

实现要点：`-f mjpeg -q:v 5 -flush_packets 1`，按 JPEG 的 `FFD8`/`FFD9` 切帧（熵编码里的 `FF00` 字节填充与 `FFD0~D7` 重启标记不会误判）。

两个实测踩到的坑，已固定在代码注释里：
- 必须加 `-pix_fmt yuvj420p`：mjpeg 编码器要求 full-range YUV，而录屏流是 limited range，否则 ffmpeg 直接报 `Non full-range YUV is non-standard` 并零输出；
- 不能加 `-fflags nobuffer`：ffmpeg 8/9 上会导致 `Output file is empty, nothing was encoded`，一帧都不编码。

**② adb 调用批处理**

`Info()` 原来要串行启动 **9 个** adb 进程（5 个 `getprop` + `wm size` + `wm density` + 3 个 `dumpsys`），Windows 上每次进程启动 80~250ms。现在合并为 **1 次** `adb shell` 脚本调用，`Refresher()` 从 4 次降到 1 次；`dumpsys` 过滤下推到设备端，不再把几百 KB 全文传回来。

**③ 前端渲染**

`Blob URL + new Image()` 每帧 → `createImageBitmap()` 后台解码 + `bitmap.close()` 显式释放，并用「只保留最新帧 + 单帧背压」避免解码排队；只给**当前激活标签页**拉流，切后台自动断开。

**④ 帧队列语义**

队列满时原实现丢弃**新帧**（用户看到越来越旧的画面），改为挤掉旧帧保留新帧，且每个订阅者独立一格队列，慢客户端不影响其它客户端。

---

## 快速开始

### 1. 运行 Windows 客户端

```
client.exe
```

- 启动后自动打开控制台：`http://127.0.0.1:8500`
- 设备通过 USB 或无线调试接入后，自动出现在左侧列表并打开标签页
- 常用参数：
  - `-port 9000` 更换端口
  - `-adb C:\...\adb.exe` / `-ffmpeg C:\...\ffmpeg.exe` 指定工具路径
  - `-nobrowser` 不自动打开浏览器
  - `-smoke` 自检：启动服务并请求 `/api/status`，通过则退出码 0
  - `-version` 查看版本
- 首次运行会在 exe 同目录生成 `config.json`（持久化 adb/ffmpeg 路径、视频/输入模式、端口）

### 2. 接入设备

**USB**：手机开启「开发者选项 → USB 调试」，插线后自动识别。

**无线（Android 11+）**：
1. 手机「开发者选项 → 无线调试」开启，点「使用配对码配对设备」，记下 **IP:配对端口** 和 **6 位配对码**
2. 控制台点「无线配对…」填入
3. 在「无线调试」详情页记下 **IP:连接端口**，点「连接设备…」填入

### 3. （可选）安装 Android Agent

```
adb install rc-agent.apk
```

安装后打开「ADB远程控制 Agent」：
1. 点「启动屏幕串流」→ 允许录屏 → 视频模式切到 **Agent串流**
2. 系统设置 → 无障碍 → 开启「ADB远程控制 Agent」→ 输入模式切到 **Agent手势**

> 两个 Agent 端口都只监听 `127.0.0.1`，电脑端必须通过 `adb forward` 访问（客户端会自动建立转发）。

---

## 控制台使用

| 区域 | 功能 |
|---|---|
| 左侧设备列表 | 在线设备自动出现；点击切换标签页 |
| 顶部工具栏 | 刷新 / 连接设备 / 无线配对 / 断开全部 / 设置 |
| 标签页工具条 | Home/返回/最近任务/菜单/电源/音量/唤醒/截图；视频模式；输入模式 |
| 画布 | 鼠标点击 = 轻触；拖拽 = 滑动（长距离自动加时长） |
| 底部输入框 | 输入文本（回车或「发送」） |
| 底部日志 | 全链路运行日志 |

**视频模式**：

| 模式 | 说明 |
|---|---|
| `自动` | 有 ffmpeg → 录屏流，否则 PNG 截图 |
| `PNG截图` | 兼容性最好（无 ffmpeg 也能用），约 4fps |
| `录屏+ffmpeg` | `screenrecord` H.264 → ffmpeg MJPEG（默认） |
| `Agent串流` | 手机 Agent 硬件编码推流（延迟最低，需装 APK） |

**输入模式**：`Shell`（`adb input`，通用）/ `Agent手势`（无障碍 `dispatchGesture`，滑动更真实）。

> 屏幕熄灭时录屏流无画面，客户端会自动降级为 PNG 并写日志；唤醒后可在界面切回。

---

## 构建

### Windows 客户端（Go 1.23+，无 cgo）

```bash
cd client
go build -trimpath -ldflags "-s -w" -o client.exe .
```

依赖仅 `github.com/gorilla/websocket`；Web 控制台通过 `go:embed` 内嵌。`ffmpeg.exe` 不入库（105MB），从 Release 附件或 [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 获取，放到 `client/third_party/ffmpeg/` 即可；缺失时自动降级 PNG 模式。

### Android Agent

```bash
cd android-agent
gradle assembleDebug   # 产物: app/build/outputs/apk/debug/app-debug.apk
```

AGP 8.0.0 / Kotlin 2.0.0 / minSdk 26 / targetSdk 33，`settings.gradle.kts` 已配阿里云镜像。

### 测试

```bash
cd client
go vet ./...
go test ./...          # 单元测试（含真实 ffmpeg 的端到端解码链路测试）
go test -race ./...    # 竞态检测
```

### 打包发布产物

```bash
pwsh scripts/build-release.ps1        # 产物输出到 dist/
```

---

## 协议与端口

| 通道 | 端口 | 绑定 | 说明 |
|---|---|---|---|
| Agent 视频 | 8585 | 设备回环 | 4 字节大端长度 + NAL；首条 `{` 开头为 JSON 配置（客户端跳过） |
| Agent 控制 | 8586 | 设备回环 | 换行分隔 JSON，应答 `{"ok":true/false}`；`tap/swipe/key` |
| 客户端控制台 | 8500 | 主机回环 | HTTP + WebSocket |

WebSocket 帧格式：
- 二进制：`0x01` + JPEG
- 文本：`0x02` + 状态 JSON

指令：`{cmd:"input",type:"tap|swipe|key|text"}`、`{cmd:"wake"}`、`{cmd:"video",mode:"auto|png|record|agent"}`、`{cmd:"inputmode",mode:"shell|agent"}`。

Agent 按键：`HOME|BACK|RECENTS|NOTIFICATIONS|QUICK_SETTINGS|POWER_DIALOG`。

---

## 安全说明

- 客户端只监听 `127.0.0.1`，且对每个请求校验 **Host**（防 DNS rebinding）与 **Origin**（防 CSRF / CSWSH）。
- Agent 的 8585/8586 只监听设备回环，必须经 `adb forward`（adb 通道本身有 USB 授权与 RSA 校验）。
- 屏幕录屏与手势权限均由用户在设备端显式授权，随时可在系统设置中撤销。
- 本工具面向**自有设备**的调试与远程协助，请勿用于未经授权的设备控制。

---

## 已知限制

1. **中文输入**：`input text` 走 `KeyCharacterMap`，只支持 ASCII，中文会被系统静默丢弃。需要中文请输入法在手机上输入（或用 Agent 侧扩展剪贴板注入）。
2. **`screenrecord` 单次上限 180 秒**、旋转会中断：客户端检测断流后自动重启，属于系统行为。
3. **Agent 旋转**：编码分辨率在连接建立时固定，旋转后需重连；客户端会在方向变化时自动重启视频流，表现为短暂黑屏而非花屏。

---

## 目录结构

```
.
├─ client/                          # Windows 客户端（Go）
│  ├─ main.go                       # 入口：信号处理、自检、优雅退出
│  ├─ internal/adb/                 # ADB 封装（设备信息批处理 / 输入 / 截图 / 录屏）
│  ├─ internal/ffmpeg/              # H.264 → MJPEG 解码与切帧
│  ├─ internal/agent/               # Agent 协议客户端
│  ├─ internal/server/              # HTTP + WebSocket + 会话 + 内嵌 Web 控制台
│  └─ third_party/ffmpeg/           # 附赠 ffmpeg（不入库）
├─ android-agent/                   # Android Agent（Kotlin）
│  └─ app/src/main/java/com/rc/agent/
│     ├─ MainActivity.kt            # 授权与引导
│     ├─ StreamService.kt           # MediaProjection H.264 推流(8585)
│     └─ ControlService.kt          # 无障碍手势服务(8586)
├─ docs/CODE_REVIEW.md              # 本次逐行审查报告
├─ scripts/build-release.ps1        # 打包发布产物
└─ README.md
```

## 排障

| 现象 | 处理 |
|---|---|
| 设备列表不出现 | 确认 USB 调试已开、线材是数据线；`adb devices -l` 应列出设备 |
| 设备显示「未授权」 | 在手机上确认 USB 调试授权弹窗 |
| 视频一直黑/无帧 | 多为屏幕熄灭（自动降级 PNG）；唤醒屏幕或手动切视频模式；查日志里的 ffmpeg 报错 |
| 无线连接失败 | 手机与电脑同一局域网；确认连接端口与配对端口不同 |
| Agent 串流无画面 | 手机先点「开始串流」并允许录屏；确认 APK 已装且 8585 转发成功 |
| Agent 手势无效 | 系统设置→无障碍 中开启本 Agent；输入模式切到 Agent手势 |
| 提示 forbidden host / origin | 用 `http://127.0.0.1:8500` 或 `http://localhost:8500` 访问，不要用其它域名/IP |
| adb 卡死/僵尸 | `adb kill-server` 后重启客户端 |
| 端口被占用 | `client.exe -port 9000` |
