# 🖥️ 远程控制系统 (Remote Control System)

> 🔗 **GitHub 仓库**: [https://github.com/tangyuan9325/remote_control](https://github.com/tangyuan9325/remote_control)

一个基于 Python 开发的局域网多功能远程控制系统。本项目包含被控端服务器（基于 Flask）和控制端 Web 界面，同时提供了一个基于 PyQt5 的本地 GUI 管理面板。用户可以通过局域网内的任意设备（手机、平板、电脑）浏览器访问 Web 界面，实现对被控电脑的实时监控、键鼠控制、系统管理、CMD 执行、多媒体调用以及文件传输等功能。

## ✨ 功能特性

### 🌐 Web 控制端 (前端)
- **实时屏幕监控**：低延迟获取被控端屏幕画面，支持自定义分辨率、帧率和画质，支持全屏查看。
- **全功能键鼠控制**：
  - **鼠标**：精准映射移动、左键点击、右键点击、滚轮滚动。
  - **键盘**：支持单字符输入、特殊按键及组合键（如 `Ctrl+C`、`Alt+Tab`）。
- **系统电源管理**：远程执行关机 (Shutdown)、重启 (Restart)、睡眠 (Sleep) 命令。
- **远程 CMD 终端**：在 Web 端直接执行系统命令并实时回显执行结果。
- **多媒体监控**：一键开启/关闭被控端摄像头画面和麦克风监听。
- **文件传输管理**：支持拖拽上传文件至被控端，并提供被控端文件的列表查看、下载和删除功能。

### 🖥️ 本地 GUI 管理端 (PyQt5)
- **深色主题 UI**：现代化的深色渐变风格界面，视觉体验舒适。
- **参数可视化配置**：自由配置服务器端口、视频分辨率（VGA 至 4K）、帧率（15-60 FPS）及 JPEG 压缩画质。
- **硬件功能开关**：一键勾选启用/禁用摄像头和麦克风。
- **系统托盘支持**：支持最小化至系统托盘后台运行，双击托盘图标恢复窗口。
- **自动化启动**：程序启动后自动隐藏至托盘，并于 5 秒后自动启动远程服务。

## 🛠️ 技术栈
- **后端框架**：Flask
- **GUI 框架**：PyQt5
- **图像处理**：OpenCV (`cv2`), Pillow (`PIL`)
- **自动化控制**：PyAutoGUI
- **音频处理**：PyAudio
- **前端**：HTML5, CSS3, JavaScript (Fetch API, XHR)

## 📦 环境依赖
运行本项目需要安装以下 Python 库（详见 `requirements.txt`）：
```text
Flask==3.0.0
Pillow==10.2.0
PyAutoGUI==0.9.54
opencv-python==4.9.0.80
numpy==1.26.4
PyQt5
PyAudio
```

## 🚀 安装与运行

### 1. 克隆项目
```bash
git clone https://github.com/tangyuan9325/remote_control.git
cd remote_control
```

### 2. 安装依赖
```bash
pip install -r requirements.txt
```
*(注：`PyAudio` 在 Windows 上若安装失败，可使用 `pipwin install pyaudio` 或下载对应的 `.whl` 文件进行本地安装)*

### 3. 启动程序
运行带有 GUI 管理界面的主程序：
```bash
python main.py
```

### 4. 访问控制端
程序启动后，GUI 界面会显示当前局域网 IP 和端口（默认 `5000`）。
在同一局域网下的手机或另一台电脑的浏览器中输入：
`http://<被控端IP>:5000`
即可进入 Web 控制面板。

## 📂 项目结构
```text
├── main.py                  # 主程序入口 (包含最新 GUI 与 Flask 逻辑)
├── remote_control.py        # 早期基础版本
├── remote_control_v1.x.py   # 历史迭代版本 (v1.1.1 - v1.5)
├── requirements.txt         # Python 依赖列表
├── packeg.bat               # PyInstaller 打包脚本
├── app.ico / icon.png       # 应用图标
├── uploads/                 # Web 端上传文件的默认存储目录
└── dist/                    # 打包生成的可执行文件 (.exe)
```

## ⚠️ 注意事项与免责声明
1. **网络安全**：本项目使用 Flask 内置的开发服务器，且未包含复杂的身份鉴权机制。**请务必仅在受信任的局域网环境内使用**，切勿将其直接暴露至公网，以免引发严重的安全隐私问题。
2. **杀毒软件拦截**：由于涉及屏幕捕获、键鼠模拟及系统电源控制，某些杀毒软件（如 Windows Defender、360 等）可能会将其误报为恶意软件。请在受控设备上将其加入白名单。
3. **合法使用**：本工具仅供个人学习、局域网内设备调试及经授权的远程协助使用。**严禁用于任何非法监控或侵犯他人隐私的活动**，使用者需自行承担相关法律责任。
4. **跨平台兼容性**：虽然代码中包含了部分 Linux/macOS 的判断，但核心功能（如 PyAutoGUI 和系统命令）主要针对 **Windows** 环境优化。

---
*Author: [汤圆](https://github.com/tangyuan9325)*
