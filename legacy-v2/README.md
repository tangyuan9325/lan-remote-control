# Legacy V2 — Go 重构版（v2.0.0）

`legacy v1.4.1 (rc14, Python/Flask)` 的 **Go 语言重写版**：更先进的架构、单二进制发布、彻底告别 PyInstaller。

## 相比 rc14 Python 版的改进
| 维度 | rc14 (Python) | legacy-v2 (Go) |
|---|---|---|
| 运行时 | Python + Flask + OpenCV + Pillow | 单个静态二进制（仅依赖 kbinani/screenshot） |
| 打包 | PyInstaller（易踩坑） | `go build` 交叉编译，一行命令出 Win/Linux/macOS 产物 |
| 并发 | GIL + 线程池 | goroutine + 原子计数 |
| 空闲算力 | 定时器轮询 | **按需采集 worker：无人观看时零 CPU**；帧差检测跳过重复编码；MJPEG 只推新帧 |
| 鉴权 | sha256+cookie | 恒定时间比较（先 SHA-256 摘要防长度泄漏）+ rc_token cookie，覆盖 `<img>` 流 |
| DoS 防护 | 无连接上限 | MJPEG 并发观众上限（8），超限返回 503 |
| 安全 | 路径穿越/CMD 已修复 | 同样保留：SafeUploadPath 符号链接校验、CMD 默认关闭、nosniff/X-Frame-Options 头 |

## 目录结构
```
legacy-v2/
├── cmd/server/main.go        # CLI 入口（flag 解析 + 优雅关闭）
├── internal/core/            # 配置/钳制、恒定时间鉴权、路径安全、token
├── internal/capture/         # 按需屏幕采集 worker（帧差跳过 + 分辨率长边钳制）
├── internal/input/           # 输入注入（Windows user32；其他平台桩）
└── internal/server/          # net/http 服务：MJPEG 流、控制端点、文件管理、Claude 风格 Web 控制台
```

## 构建与运行
```bash
cd legacy-v2
go build -o dist/legacy-v2-server ./cmd/server              # 本机
GOOS=windows GOARCH=amd64 go build -o dist/legacy-v2-server.exe ./cmd/server  # Windows

./dist/legacy-v2-server -port 5000 -password "your-secret" \
    -fps 15 -quality 60 -width 1280 -height 720
# 浏览器打开 http://<IP>:5000 ，口令门进入 Claude 风格控制台
```

### CLI 参数
| 参数 | 默认 | 说明 |
|---|---|---|
| `-port` | 5000 | HTTP 端口 |
| `-password` | 空 | 访问密码（留空=无鉴权，启动时会打警告） |
| `-enable-cmd` | false | 开启远程命令执行端点（危险，显式 opt-in） |
| `-fps` | 15 | 目标帧率 1–60 |
| `-quality` | 60 | JPEG 质量 10–95 |
| `-width/-height` | 1280x720 | 推流分辨率（长边自动钳到 1920） |
| `-uploads` | ./uploads | 上传目录 |

## API（与 rc14 兼容）
`GET /`（Web 控制台）· `/api_status` · `/video_feed` `/camera_feed` `/audio_feed`（MJPEG，需鉴权）·
`POST /mouse_move /mouse_click /mouse_scroll /keyboard /set_quality /toggle_camera /toggle_microphone` ·
`/upload_file /list_files /download_file/<name> /delete_file/<name>` · `/execute_cmd`（需 -enable-cmd）

## 测试
```bash
go test ./...   # server: 鉴权/路径安全/并发上限/安全头; core: 参数钳制/密码校验
go vet ./...
```
