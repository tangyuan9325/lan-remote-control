# 代码审查报告 · ADB 远程控制台 v1.1.0

审查范围：`client/`（Go，2091 行）与 `android-agent/`（Kotlin，639 行），逐文件逐函数通读。
审查目标：正确性缺陷、安全漏洞、算法与性能优化。

审查基线为仓库首个提交（上游原始代码），本报告中的「原实现」均指该基线。
所有修复都能用 `git diff <基线> HEAD` 逐条对照。

---

## 一、严重问题（功能失效 / 可被远程接管）

### S1. Android Agent 缺少 `INTERNET` 权限，串流与手势端口根本无法监听

- 位置：`android-agent/app/src/main/AndroidManifest.xml`
- 现象：清单里只有 `FOREGROUND_SERVICE`、`FOREGROUND_SERVICE_MEDIA_PROJECTION`、`POST_NOTIFICATIONS` 三项权限。
- 后果：Android 的 `INTERNET` 权限通过补充 GID 限制 `AF_INET` socket 的创建，**监听端口同样受限**。缺该权限时 `ServerSocket()` 直接抛 `socket failed: EACCES (Permission denied)`，而 `StreamService`/`ControlService` 都把 `IOException` 吞进日志里，于是表现为「点击开始串流没报错，但电脑端一直连不上」。
- 证据：`app/build/intermediates/merged_manifest/debug/AndroidManifest.xml` 中确实只有 3 条 `uses-permission`。
- 修复：补 `<uses-permission android:name="android.permission.INTERNET" />`。
- 备注：默认视频模式是 `auto → record`（走 adb screenrecord），所以这条路径平时被掩盖，只有切到「Agent 串流」才会暴露。

### S2. WebSocket 的 `CheckOrigin` 恒为 `true`，任意网页可跨站接管手机

- 位置：`client/internal/server/server.go`，`websocket.Upgrader.CheckOrigin`
- 现象：`CheckOrigin: func(r *http.Request) bool { return true }`。
- 后果：WebSocket **不受同源策略保护**。你只要在浏览器里打开任意恶意页面，它就能 `new WebSocket('ws://127.0.0.1:8500/ws/device/<序列号>')`，拿到连续屏幕截图，并发送 `tap/swipe/text` 指令——等于整台手机被对方操作。序列号可通过 `/api/devices` 或暴力枚举获得。
- 修复：按 Host/Origin 白名单校验（见 S3），WebSocket 升级前同样校验。

### S3. 只校验「监听 127.0.0.1」不足以防护 DNS rebinding

- 位置：`client/internal/server/server.go`（新增 `guard` 中间件）
- 现象：原实现仅靠 `ListenAndServe("127.0.0.1:8500")`。但恶意域名可以解析到 `127.0.0.1`，浏览器发出的请求 Host 仍是攻击者域名，服务端照样应答，攻击者即可读取 `/api/logs`、`/api/devices` 甚至发起写操作。
- 修复：
  - `hostAllowed()`：Host 必须是 `127.0.0.1` / `localhost` / `::1` 且端口匹配，否则 403；
  - `originAllowed()`：写操作（非 GET/HEAD）与 WebSocket 升级额外校验 Origin，`null` 一律拒绝。
- 已实测：伪造 Host → 403；跨站 POST → 403；跨站 WS 升级 → 拒绝；同源 → 200。

### S4. Android 端两个服务绑定 `0.0.0.0` 且无任何鉴权

- 位置：`StreamService.kt` `ServerSocket(VIDEO_PORT)`、`ControlService.kt` `ServerSocket(CONTROL_PORT)`
- 后果：同一 Wi-Fi 下任何设备都能连 8585 看你的屏幕、连 8586 向你的手机注入点击与滑动，无需任何口令。
- 修复：改为 `bind(InetSocketAddress(InetAddress.getLoopbackAddress(), PORT))`，只监听回环，必须经 `adb forward` 才能访问；adb 通道本身已有 USB 授权/RSA 校验保护。
- 附带收益：Android 7+ 对应用绑定 `0.0.0.0` 本就有限制，改回环也顺带提高了兼容性。

---

## 二、功能性缺陷

### B1. 每一帧都因索引错位而「错位重排」

- 位置：`client/internal/ffmpeg/ffmpeg.go` `ReadFrame()`（原实现）
- 原代码：
  ```go
  buf := make([]byte, w*h*3)          // BGR24
  img := image.NewRGBA(...)           // 4 字节/像素
  dp := img.Pix
  for i := 0; i+2 < len(buf); i += 3 {
      dp[i]   = buf[i+2] // R
      dp[i+1] = buf[i+1]
      dp[i+2] = buf[i]   // B
      dp[i+3] = 255      // A
  }
  ```
  `buf` 是 3 字节/像素，`dp` 是 4 字节/像素，但两者共用同一个下标 `i`。第 k 个像素写的是 `dp[3k..3k+3]`，而它实际应写入 `dp[4k..4k+3]`——每写一个像素就多挤占下一像素的通道，整帧呈对角偏色错位。
- 修复：整条链路改为 ffmpeg 直接输出 MJPEG（见 P1），Go 端不再做像素格式转换，该函数已删除。

### B2. 单个 Session 只保存一个广播回调，多标签页互相踢掉

- 位置：`client/internal/server/session.go` `SetBroadcast`（原实现）
- 现象：`broadcast` 是单个函数字段。打开第二个标签页会覆盖第一个的回调；任一个标签页关闭时执行 `defer sess.SetBroadcast(nil)`，把所有标签页的推流一起置空。
- 修复：改为订阅者集合 `map[*Subscriber]struct{}`，每个浏览器连接独立订阅/退订，互不影响；并新增回归测试 `TestSessionMultiSubscriber`。

### B3. 多处数据竞态（`-race` 可复现）

| 字段 | 写入方 | 读取方 |
|---|---|---|
| `Session.videoMode` / `inputMode` | WebSocket 指令协程 | 视频协程、HTTP 处理协程 |
| `Session.info` | `infoLoop` | `StatusMap`/`Info`/`handleDevice` |
| `Session.ctrl` | 多个 WS 协程 | 同上 |
| `Server.devices` | `pollLoop` | `handleDisconnect` |
| `Server.adb` | `handleSettings` | `pollLoop` |

- 修复：模式用 `sync.RWMutex`，设备信息用 `RWMutex`，Agent 控制连接加锁并在失效时重建，`devices` 与 `adb` 分别加锁。`go test -race ./...` 现为全绿。

### B4. Agent 控制连接失效后永不重建

- 位置：`client/internal/agent/agent.go`、`session.go` `agentCtrl()`
- 现象：`s.ctrl` 一旦建立就永久缓存。手机上 Agent 重启或端口转发重建后，旧连接不可用，但代码始终复用它：每次点击都先失败一次（最长 5 秒超时）再回落 shell。
- 修复：新增 `Healthy()` 与 `markBroken()`，I/O 出错即标记并关闭；`agentCtrl()` 发现不健康就重建连接。同时补 `SetWriteDeadline`，避免写阻塞。

### B5. 串流服务只 `accept()` 一次，之后必须重新授权录屏

- 位置：`StreamService.kt` `startServer()`（原实现）
- 现象：`accept()` 一次 → `stream()` → `finally { stopSelf() }`。客户端重启、切换视频模式、屏幕旋转都会断开连接，服务随之自杀；而 `MediaProjection` 授权令牌每次都要用户手动确认，于是用户被迫反复点「允许录屏」。
- 修复：改为 `while (runningFlag) { accept(); stream() }`，客户端断开后继续等待下一个连接，`MediaProjection` 全程复用。
- 同时补上 `MediaProjection.registerCallback()`：Android 14 起要求在 `createVirtualDisplay` 之前注册，否则抛 `IllegalStateException`；用户点「停止共享」时也能正确收尾。

### B6. 手势服务串行处理客户端，旧连接会把新连接饿死

- 位置：`ControlService.kt` `handleClient()`（原实现）
- 现象：`while (running) { accept(); handleClient(client) }`——`handleClient` 内部阻塞读，只要旧连接不主动关闭（例如客户端崩溃留下的半开连接），`accept()` 永远不会再被调用。
- 修复：每个连接独立线程处理，并加 `soTimeout = 120s`，半开连接到期自动回收。

### B7. 退出时不回收子进程

- 位置：`client/main.go`（原实现 `select {}`）
- 现象：进程只能被强杀，`ffmpeg.exe` 与手机上的 `screenrecord` 会成为孤儿；手机上表现为「一直有录屏图标、耗电」。
- 修复：捕获 `SIGINT/SIGTERM`，`Server.Shutdown()` 依次关停 HTTP、停止所有 Session（`killCurrent` 杀 ffmpeg + screenrecord）、关闭订阅者。

### B8. 配置文件非原子写入

- 位置：`client/internal/server/config.go` `Save()`
- 现象：`os.WriteFile` 直接覆盖，写入过程中断电/崩溃会留下半截 JSON；下次 `LoadConfig` 解析失败后静默返回默认值——用户的 adb/ffmpeg 路径配置丢失且无提示。
- 修复：写 `.tmp` 再 `os.Rename`（原子替换）；解析失败时显式回落默认值；端口越界回落到 8500。

### B9. 输入参数未校验，超大 duration 会让 adb 挂满 30 秒

- 位置：`client/internal/server/server.go` `handleWSCommand`、`session.go` `Swipe`
- 现象：`duration` 直接透传，`input swipe ... 999999999` 会让 `input` 命令长时间不返回，占住一个 30 秒超时的同步 adb 调用；负坐标同样会被拼进命令。
- 修复：坐标按屏幕宽高校验（越界直接丢弃），`duration` 钳制到 20~3000ms；`connect/pair/disconnect` 的 `host` 用 `validHostPort` 校验（同时阻断以 `-` 开头的参数注入）。

### B10. 前端每次开关标签页都累积 `window` 监听器

- 位置：`webui/app.js`
- 现象：`mousemove`/`mouseup` 挂在 `window` 上，`closeTab` 不解绑，反复开关标签页后监听器线性增长。
- 修复：保存引用并在 `closeTab` 中 `removeEventListener`。

---

## 三、算法与性能优化

### P1. 解码链路：rawvideo → MJPEG 直通（收益最大）

原链路：`H.264 → ffmpeg 解码 → bgr24 rawvideo → 管道 → Go 逐像素 BGR→RGBA → Go jpeg.Encode → 浏览器`

问题有三层：
1. **管道带宽**：1080p 每帧 `1920×1080×3 = 6.2MB`，30fps 即 **186MB/s** 在内核管道里来回搬。
2. **两次全帧遍历**：Go 侧格式转换 + JPEG 编码，都是单线程逐像素。
3. **固定宽高**：rawvideo 的帧长依赖预先探测的 `w×h`，旋转或分辨率变化后帧边界错位，只能靠重启视频流绕过。

新链路：`H.264 → ffmpeg 解码 + MJPEG 编码（SIMD 多线程）→ 管道 → Go 只做 SOI/EOI 切帧 → 浏览器`

| 指标 | 原实现 | 新实现 | 变化 |
|---|---|---|---|
| 管道数据量/帧（1080p） | ~6.2 MB | ~0.1 MB | ≈60× 下降 |
| Go 侧每帧像素遍历 | 2 次 | 0 次 | — |
| 每帧分配 | RGBA 8.3MB + JPEG 缓冲 | 一个 JPEG 切片 | 大幅下降 |
| 分辨率变化 | 帧长错位，需重启 | 自适应 | 修复 |

前置参数也按低延迟重调（与 scrcpy 生态一致）：

```
-probesize 32k -analyzeduration 0 -fflags nobuffer+discardcorrupt -flags low_delay
-f h264 -i pipe:0 -f mjpeg -q:v 5 -flush_packets 1 pipe:1
```

帧边界识别：JPEG 的 SOI=`FF D8`、EOI=`FF D9`。熵编码数据中的 `FF` 一律字节填充为 `FF 00`，重启标记是 `FF D0~D7`，因此扫描 `FF D9` 不会误判——单测覆盖了字节填充与重启标记两种情况。

### P2. adb 调用批处理：Info 9 次 → 1 次，Refresher 4 次 → 1 次

原实现每个字段一次 `adb shell`，Windows 上每次进程启动 80~250ms：

- `Info()`：`getprop ×5 + wm size + wm density + dumpsys input + dumpsys window + dumpsys battery` ≈ **9 次进程**
- `Refresher()`（每 5 秒/台）：**4 次进程**

修复：把多条命令拼成一段脚本，**一次** `adb shell` 执行，用 `@@KEY@@` 标记分段解析；并把 `dumpsys` 的过滤下推到设备端（`| grep ... | head -1`），避免把几百 KB 的 dumpsys 全文经 USB/Wi-Fi 传回来。

预期：单设备首连信息采集从 ~1~2s 降到 ~0.2s；每设备每 5 秒的刷新开销降到 1/4。

### P3. 截图路径去掉逐像素转换

`toRGBA()` 用双重循环 + `img.At()`/`Set()`，1080p 一帧 200 万次接口调用。而 `png.Encode`/`jpeg.Encode` 本来就接受任意 `image.Image`，这次转换纯属浪费——直接删除，`Screenshot()` 返回 `image.Image`。

### P4. 前端渲染：`Blob URL + new Image` → `createImageBitmap` + 单帧背压

原实现每帧 `URL.createObjectURL` + `new Image()` + `onload`：
- 每帧创建/销毁一个 object URL，长跑后内存曲线呈锯齿状；
- 解码在主线程，20fps 下持续抢占主线程；
- 没有背压，解码慢于到达速度时 `onload` 回调排队堆积。

新实现：
- `createImageBitmap(blob)` 在解码线程解码，用完 `bitmap.close()` 显式释放；
- 只保留「最新一帧」（`sess.pending` 被后到帧覆盖），并用 `drawing` 标志保证同一时刻只有一帧在解码——天然背压；
- 保留 `Image + objectURL` 作为 `createImageBitmap` 不可用时的降级路径，并正确 `revokeObjectURL`。

### P5. 只为「激活的标签页」拉流

原实现每个在线设备都开一条 WebSocket 并以 20fps 推流。同时接 4 台设备时，浏览器要解码 80 帧/秒，客户端也要发 80 帧/秒。

修复：切换标签页时只保留当前页的连接，其余主动 `close()`；页面切到后台（`visibilitychange`）也断开，回到前台自动重连；意外断开后 1.5 秒自动重连。

### P6. 帧队列语义：丢弃最新帧 → 保留最新帧

原 `pushFrame` 用 `select { case ch <- fr: default: }`——队列满时**丢弃新帧**，等于让用户看越来越旧的画面。现在改为「挤掉旧帧、放入新帧」，并给每个订阅者独立的一格队列，慢客户端不会拖累其它客户端。

### P7. 其他微优化

- `agent.nextChunk` / `annexBReader`：复用接收缓冲，去掉每帧两次分配（30fps 下是持续的 GC 压力）。
- `handleWS` 用 `NextWriter` 分两段写帧头与帧体，省掉每帧一次 `append` 拼接。
- PNG 模式下无订阅者时跳过截图，省掉一次 `adb exec-out screencap`。
- `Clients/Server` 增加 `ReadHeaderTimeout`、`IdleTimeout`、`bytes` 上限与 WS 读限（64KB）、20 秒 Ping 心跳。

---

## 四、其他小问题

| # | 位置 | 问题 | 处理 |
|---|---|---|---|
| 1 | `adb.Devices()` | `strings.HasPrefix(line, " ")` 分支恒为 false（已 TrimSpace），且未设置 Scanner 上限 | 删除死分支；`sc.Buffer` 提到 1MB |
| 2 | `adb.Screenshot()` | 原始像素格式只按 12 字节头解析；Android 9+ 的 16 字节头会整帧偏移 | 用「总长精确匹配」区分 12/16 字节头；格式非 RGBA_8888 时明确报错 |
| 3 | `StreamService.createEncoder()` | `SDK>=30` 与 `else` 两个分支代码完全一样 | 合并 |
| 4 | `ControlService.process()` | 错误信息只把 `"` 替换成 `'`，反斜杠/换行仍会破坏 JSON | 改用 `JSONObject` 生成应答 |
| 5 | `ControlService.dispatchGesture()` | 返回值被忽略，无论成功失败都回 `{"ok":true}` | 返回真实结果，失败时带 error |
| 6 | `MainActivity.updateStatus()` | if/else 两个分支内容完全相同 | 合并 |
| 7 | `session.drain()` | 未使用的死代码 | 删除 |
| 8 | `StreamService` | `catch (IOException)` 捕获不到编码器的 `IllegalStateException`，线程直接崩 | 改 `catch (Throwable)` |
| 9 | `version` | `main.go` 与 `handleStatus` 各自硬编码 "1.0.0" | 统一到 `server.Version` |
| 10 | 文本输入 | `input text` 只能输 ASCII，中文被静默丢弃 | 前端提示 + 服务端日志提示（详见「已知限制」） |

---

## 五、已知限制（本次未修改）

1. **中文等非 ASCII 文本无法通过 `input text` 输入**。`input text` 走 `KeyCharacterMap`，非 ASCII 字符会被系统丢弃。要支持中文需要 ADBKeyboard 之类的输入法 APK，或让 Agent 侧做剪贴板注入——两者都改变了「零安装」的设计前提，本次仅在 UI 和日志中给出明确提示。
2. **`adb shell` 的参数拼接方式**：官方文档明确 adb 不做二次转义（`adb shell setprop foo 'a b'` 会失败，需写两层引号），因此文本输入必须自己做一次 shell 引号包裹。这一点原实现的思路是对的，本次保留并补了注入用例测试。
3. **`screenrecord` 单次上限 180 秒**、旋转会中断：仍由客户端检测断流后自动重启，属于系统行为。
4. **Agent 端旋转**：编码分辨率在会话建立时固定，旋转后需重连才能拿到新分辨率；客户端现在会在方向变化时自动重启视频流，因此表现为短暂黑屏而非花屏。
