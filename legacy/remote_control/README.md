# 🖥️ 远程控制系统 (Remote Control System) · v1.4.1 Claude 重构版

> 🔗 **GitHub 仓库**: [https://github.com/tangyuan9325/remote_control](https://github.com/tangyuan9325/remote_control)
> ⚠️ 本目录已并入大仓库 `lan-remote-control`，作为 **方向二（早期实验项目）** 存档维护；主线开发请看仓库根目录的 Go + Flutter 方案。

一个基于 Python 开发的局域网多功能远程控制系统。被控端服务器基于 Flask，控制端为浏览器 Web 界面，另提供 PyQt5 本地 GUI 管理面板。v1.4 起完成整体重构：**Claude/Anthropic 风格 UI、按需采集算力优化、多层安全防护与 PyInstaller 一键打包修复**。

## 📂 项目结构（重构后）

```text
legacy/remote_control/
├── rc14/                      # ✅ 推荐入口：v1.4.1 重构包
│   ├── main.py                #    CLI 入口（GUI / --headless）
│   ├── __main__.py            #    python -m rc14 支持
│   ├── core.py                #    配置、屏幕/摄像头/麦克风工作线程、安全工具函数
│   ├── server.py              #    Flask 路由：鉴权、文件、输入、电源、CMD
│   ├── webui.py               #    Claude 风格 Web 控制台（内嵌单页前端）
│   └── gui.py                 #    Claude 风格 PyQt5 管理面板（全局 QSS）
├── tests/test_rc14.py         # 自检脚本（15 项测试）
├── build_rc14.bat             # ✅ PyInstaller 一键打包（配合 rc14.spec）
├── rc14.spec                  # ✅ 新 spec：收集 flask/cv2/PyQt5 动态依赖与图标
├── remote_control_v1.4.py     # 历史单体版本（保留参考，含弃用守卫）
├── main.py / remote_control*.py  # 历史版本（已全部加弃用守卫，直接运行仅打印迁移提示）
├── requirements.txt           # Python 依赖
└── app.ico / icon.png         # 应用图标
```

## 🚀 安装与运行

```bash
cd legacy/remote_control
pip install -r requirements.txt

python -m rc14.main                        # GUI 控制面板（PyQt5，Claude 风格）
python -m rc14.main --headless \
    --port 5000 --password s3cret          # 无 GUI，仅启动服务器
python tests/test_rc14.py                  # 运行自检（15 项）
build_rc14.bat                             # Windows 一键打包 EXE
```

程序启动后，在同一局域网设备的浏览器访问 `http://<被控端IP>:5000`；若设置了密码，Web 端会先弹出口令门，通过后写入 `rc_token` Cookie，后续画面流与 API 均自动携带凭证。

## ✨ v1.4.1 重构要点

### 🎨 UI — Claude 风格设计语言
- **Web 控制台**（`webui.py`）：暖米色背景 `#faf9f5`、衬线标题、赤陶色（terracotta）强调色、细发丝边框卡片、粘性顶栏——以平静的 Anthropic/Claude 观感替换旧深色渐变主题。
- **PyQt5 面板**（`gui.py`）：同一套视觉语言通过 **一份全局 QSS** 应用到所有控件（替代旧版几十处逐控件 setStyleSheet），含"按需采集 · 空闲零占用"状态标识。

### ⚡ 算力优化（大幅降低运行时占用）
- **按需采集**：屏幕/摄像头/麦克风工作线程采用 touch/viewers 引用机制——**无观看者时 worker 挂起等待，CPU/GPU 占用趋近于 0**；有请求时自动唤醒。
- **帧差检测**：`_sample` + `_changed` 对画面采样比对，静止画面跳过编码与推流（跳变推送）。
- **分辨率钳制**：长边 ≤1920，避免 4K 全屏编码烧 CPU。
- **高效编码路径**：优先 dxcam（DXGI），回退 PIL/mss；JPEG 质量可调。
- **MJPEG 流护栏**：`mjpeg_stream` 带最大时长（300s）与停止条件，防止连接泄漏。

### 🔒 安全加固（修复旧版漏洞）
- **访问密码**：`--password` 启用；`check_password` 恒定时间比较防时序侧信道；API 与画面流经 `rc_token` Cookie / password 参数双重校验。
- **路径穿越拦截**：`safe_upload_path` 净化上传名并强制限定在 uploads 目录内。
- **上传防护**：文件名清洗 + 大小上限校验 + 拒绝符号链接。
- **远程 CMD 默认禁用**：需 `--enable-cmd` 显式开启（旧版存在任意命令执行 RCE）。
- **XSS 消毒**：文件列表等所有外部数据经转义后渲染。
- **弃用守卫**：`main.py`、`remote_control_v1.x.py` 等历史文件在 import 前即拦截，只打印迁移提示并退出，杜绝误跑无密码/带 RCE 的旧代码。

### 📦 PyInstaller 打包修复
- 新增 `rc14.spec` + `build_rc14.bat`：修复旧 `packeg.bat`/spec 的入口路径错误（ModuleNotFoundError）、Qt 平台插件缺失问题。
- `--collect-all` 收集 cv2 / PyQt5 / PIL 的动态导入依赖；自动清理陈旧 build 产物；打包前校验 python 与 PyInstaller 环境并给出清晰错误。

## 🛠️ 技术栈
Flask · PyQt5 · OpenCV (cv2) · Pillow · NumPy · PyAutoGUI · PyAudio · HTML5/CSS3/JS

## ⚠️ 注意事项与免责声明
1. **网络安全**：即便经过 v1.4 加固，本项目仍使用 Flask 内置服务器，**请务必仅在受信任的局域网环境内使用并设置密码**，切勿暴露至公网。
2. **杀毒软件拦截**：涉及屏幕捕获、键鼠模拟及电源控制，可能被 Defender/360 误报，请在受控设备上加入白名单。
3. **合法使用**：仅供个人学习、局域网调试及经授权的远程协助；严禁非法监控或侵犯隐私。
4. **跨平台**：核心功能针对 **Windows** 优化（dxcam、电源命令等），Linux/macOS 可运行但部分能力受限。

---
*Author: [汤圆](https://github.com/tangyuan9325)*
