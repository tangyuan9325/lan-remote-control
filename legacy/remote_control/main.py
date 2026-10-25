def _deprecated():
    import textwrap
    print(textwrap.dedent("""\
        ============================================================
        [已弃用] main.py 是旧版单体入口（Flask + PyQt5 混写，存在
        无密码、路径穿越、shell=True RCE 等已知漏洞，仅作历史存档）。

        请改用重构后的安全版本 v1.4 (rc14 包)：
            python -m rc14.main                     # GUI 控制面板
            python -m rc14.main --headless \
                --port 5000 --password s3cret       # 无 GUI，仅服务器
            python tests/test_rc14.py               # 运行自检
            build_rc14.bat                          # PyInstaller 一键打包
        ============================================================"""))
    raise SystemExit(1)


_deprecated()  # guard FIRST: this archive file must never run its legacy server

# ---- below is the archived original code (kept for reference only) ----
import os
import io
import base64
import threading
import socket
import sys
from flask import Flask, render_template_string, request, jsonify, Response
from PIL import ImageGrab
import pyautogui
import cv2
import numpy as np
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                           QHBoxLayout, QPushButton, QLabel, QLineEdit, 
                           QGroupBox, QFormLayout, QMessageBox, QStatusBar)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QIcon

app = Flask(__name__)
pyautogui.FAILSAFE = False  # 禁用鼠标移动到左上角的fail-safe

# 获取本机IP地址
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return '127.0.0.1'

# 屏幕捕获线程
class ScreenCaptureThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self.daemon = True
        self.running = True
        self.last_frame = None
        self.lock = threading.Lock()
    
    def run(self):
        while self.running:
            try:
                # 使用PIL捕获屏幕
                screenshot = ImageGrab.grab()
                # 转换为OpenCV格式
                img = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
                # 调整大小以提高传输效率
                img = cv2.resize(img, (800, 600))
                # 转换为JPEG格式
                _, buffer = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 70])
                with self.lock:
                    self.last_frame = buffer.tobytes()
            except Exception as e:
                print(f"屏幕捕获错误: {e}")
    
    def stop(self):
        self.running = False
    
    def get_frame(self):
        with self.lock:
            return self.last_frame if self.last_frame else b''

# 全局变量
screen_thread = None
flask_thread = None
server_running = False

# HTML模板
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>远程控制</title>
    <style>
        body {
            font-family: 'Arial', sans-serif;
            margin: 0;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        .container {
            background: white;
            border-radius: 15px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.1);
            padding: 30px;
            width: 90%;
            max-width: 1000px;
            margin-top: 20px;
        }
        h1 {
            color: #333;
            text-align: center;
            margin-bottom: 30px;
            font-size: 2.5em;
            background: linear-gradient(45deg, #667eea, #764ba2);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .screen-container {
            position: relative;
            border: 2px solid #667eea;
            border-radius: 10px;
            overflow: hidden;
            margin: 20px 0;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }
        #screen {
            width: 100%;
            display: block;
        }
        .controls {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-top: 20px;
        }
        .control-btn {
            padding: 15px;
            border: none;
            border-radius: 10px;
            color: white;
            font-size: 16px;
            cursor: pointer;
            transition: all 0.3s ease;
            box-shadow: 0 5px 15px rgba(0,0,0,0.2);
        }
        .mouse-btn {
            background: linear-gradient(45deg, #ff6b6b, #ee5a5a);
        }
        .keyboard-btn {
            background: linear-gradient(45deg, #4ecdc4, #44a08d);
        }
        .system-btn {
            background: linear-gradient(45deg, #ffd166, #ffc300);
        }
        .control-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(0,0,0,0.3);
        }
        .status {
            text-align: center;
            margin-top: 20px;
            font-size: 1.1em;
            color: #666;
        }
        .connection-info {
            background: #f8f9fa;
            padding: 15px;
            border-radius: 10px;
            margin: 20px 0;
            text-align: center;
            border: 1px solid #ddd;
        }
        @media (max-width: 768px) {
            .controls {
                grid-template-columns: 1fr;
            }
            .container {
                padding: 20px;
                margin: 10px;
            }
        }
    </style>
</head>
<body>
    <h1>🖥️ 远程控制系统</h1>
    <div class="container">
        <div class="connection-info">
            <p>连接地址: <strong>http://{{ ip }}:{{ port }}</strong></p>
            <p>当前状态: <span id="connection-status" style="color: green;">✅ 已连接</span></p>
        </div>
        
        <div class="screen-container">
            <img id="screen" src="/video_feed" alt="远程屏幕">
        </div>
        
        <div class="controls">
            <button class="control-btn mouse-btn" onclick="showMouseControls()">🖱️ 鼠标控制</button>
            <button class="control-btn keyboard-btn" onclick="showKeyboardControls()">⌨️ 键盘控制</button>
            <button class="control-btn system-btn" onclick="showSystemControls()">⚙️ 系统控制</button>
        </div>
        
        <div class="status" id="status">就绪</div>
    </div>

    <script>
        let screenElement = document.getElementById('screen');
        let statusElement = document.getElementById('status');
        let lastMouseX = 0;
        let lastMouseY = 0;
        
        // 鼠标移动事件
        screenElement.addEventListener('mousemove', function(e) {
            const rect = screenElement.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            
            // 发送鼠标移动事件到服务器
            fetch('/mouse_move', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    x: x,
                    y: y,
                    screen_width: rect.width,
                    screen_height: rect.height
                })
            });
        });
        
        // 鼠标点击事件
        screenElement.addEventListener('click', function(e) {
            const rect = screenElement.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            
            fetch('/mouse_click', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    x: x,
                    y: y,
                    screen_width: rect.width,
                    screen_height: rect.height
                })
            });
        });
        
        // 右键点击事件
        screenElement.addEventListener('contextmenu', function(e) {
            e.preventDefault();
            const rect = screenElement.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            
            fetch('/mouse_right_click', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    x: x,
                    y: y,
                    screen_width: rect.width,
                    screen_height: rect.height
                })
            });
        });
        
        // 滚轮事件
        screenElement.addEventListener('wheel', function(e) {
            fetch('/mouse_scroll', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    delta: e.deltaY
                })
            });
        });
        
        // 显示鼠标控制
        function showMouseControls() {
            statusElement.textContent = '🖱️ 鼠标控制模式';
        }
        
        // 显示键盘控制
        function showKeyboardControls() {
            const key = prompt('请输入要发送的按键:');
            if (key) {
                fetch('/keyboard', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ key: key })
                });
                statusElement.textContent = `⌨️ 已发送按键: ${key}`;
            }
        }
        
        // 显示系统控制
        function showSystemControls() {
            const action = prompt('请输入系统命令 (如: shutdown, restart, sleep):');
            if (action) {
                fetch('/system', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ action: action })
                });
                statusElement.textContent = `⚙️ 执行系统命令: ${action}`;
            }
        }
        
        // 定期刷新屏幕
        setInterval(function() {
            screenElement.src = '/video_feed?_=' + new Date().getTime();
        }, 100);
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    local_ip = get_local_ip()
    return render_template_string(HTML_TEMPLATE, ip=local_ip, port=5000)

@app.route('/video_feed')
def video_feed():
    if screen_thread:
        frame = screen_thread.get_frame()
        if frame:
            return Response(frame, mimetype='image/jpeg')
    return '', 404

@app.route('/mouse_move', methods=['POST'])
def mouse_move():
    data = request.json
    x = data['x']
    y = data['y']
    screen_width = data['screen_width']
    screen_height = data['screen_height']
    
    # 转换为实际屏幕坐标
    actual_x = x * (pyautogui.size().width / screen_width)
    actual_y = y * (pyautogui.size().height / screen_height)
    
    pyautogui.moveTo(actual_x, actual_y)
    return jsonify({'status': 'success'})

@app.route('/mouse_click', methods=['POST'])
def mouse_click():
    data = request.json
    x = data['x']
    y = data['y']
    screen_width = data['screen_width']
    screen_height = data['screen_height']
    
    actual_x = x * (pyautogui.size().width / screen_width)
    actual_y = y * (pyautogui.size().height / screen_height)
    
    pyautogui.click(actual_x, actual_y)
    return jsonify({'status': 'success'})

@app.route('/mouse_right_click', methods=['POST'])
def mouse_right_click():
    data = request.json
    x = data['x']
    y = data['y']
    screen_width = data['screen_width']
    screen_height = data['screen_height']
    
    actual_x = x * (pyautogui.size().width / screen_width)
    actual_y = y * (pyautogui.size().height / screen_height)
    
    pyautogui.rightClick(actual_x, actual_y)
    return jsonify({'status': 'success'})

@app.route('/mouse_scroll', methods=['POST'])
def mouse_scroll():
    data = request.json
    delta = data['delta']
    pyautogui.scroll(int(-delta/50))  # 调整滚轮灵敏度
    return jsonify({'status': 'success'})

@app.route('/keyboard', methods=['POST'])
def keyboard():
    data = request.json
    key = data['key']
    try:
        pyautogui.write(key)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/system', methods=['POST'])
def system():
    data = request.json
    action = data['action'].lower()
    
    if action == 'shutdown':
        os.system('shutdown /s /t 0' if os.name == 'nt' else 'shutdown now')
    elif action == 'restart':
        os.system('shutdown /r /t 0' if os.name == 'nt' else 'sudo reboot')
    elif action == 'sleep':
        os.system('rundll32.exe powrprof.dll,SetSuspendState 0,1,0' if os.name == 'nt' else 'systemctl suspend')
    
    return jsonify({'status': 'success'})

# Flask服务器线程
class FlaskThread(QThread):
    server_started = pyqtSignal(str, int)
    server_stopped = pyqtSignal()
    
    def __init__(self, port=5000):
        super().__init__()
        self.port = port
        self.server = None
        self.running = False
    
    def run(self):
        global screen_thread, server_running
        try:
            # 启动屏幕捕获线程
            screen_thread = ScreenCaptureThread()
            screen_thread.start()
            
            local_ip = get_local_ip()
            self.server_started.emit(local_ip, self.port)
            
            self.running = True
            server_running = True
            
            # 启动Flask服务器
            app.run(host='0.0.0.0', port=self.port, threaded=True, debug=False)
            
        except Exception as e:
            print(f"服务器启动错误: {e}")
        finally:
            server_running = False
    
    def stop(self):
        global screen_thread
        if screen_thread:
            screen_thread.stop()
            screen_thread = None
        
        if self.running:
            # 发送一个请求来优雅地停止服务器
            try:
                import requests
                requests.get(f'http://127.0.0.1:{self.port}/shutdown', timeout=1)
            except:
                pass
            
            self.running = False
            self.server_stopped.emit()

# PyQt5主窗口
class RemoteControlGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("远程控制被控端")
        self.setMinimumSize(600, 400)
        
        # 创建中心部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(30, 30, 30, 30)
        
        # 标题
        title_label = QLabel("🖥️ 远程控制被控端")
        title_label.setFont(QFont("Arial", 24, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("color: #667eea; margin: 10px 0;")
        main_layout.addWidget(title_label)
        
        # 服务器配置组
        config_group = QGroupBox("服务器配置")
        config_layout = QFormLayout()
        
        self.port_input = QLineEdit("5000")
        self.port_input.setPlaceholderText("请输入端口号 (1024-65535)")
        config_layout.addRow("端口号:", self.port_input)
        
        config_group.setLayout(config_layout)
        main_layout.addWidget(config_group)
        
        # 状态显示
        status_group = QGroupBox("服务器状态")
        status_layout = QVBoxLayout()
        
        self.status_label = QLabel("🔴 服务器未运行")
        self.status_label.setFont(QFont("Arial", 14))
        self.status_label.setAlignment(Qt.AlignCenter)
        status_layout.addWidget(self.status_label)
        
        self.ip_label = QLabel("IP地址: -")
        self.ip_label.setFont(QFont("Arial", 12))
        status_layout.addWidget(self.ip_label)
        
        self.url_label = QLabel("访问地址: -")
        self.url_label.setFont(QFont("Arial", 12))
        self.url_label.setStyleSheet("color: #44a08d;")
        status_layout.addWidget(self.url_label)
        
        status_group.setLayout(status_layout)
        main_layout.addWidget(status_group)
        
        # 控制按钮
        button_layout = QHBoxLayout()
        button_layout.setSpacing(20)
        
        self.start_btn = QPushButton("🟢 启动服务器")
        self.start_btn.setFont(QFont("Arial", 14))
        self.start_btn.setStyleSheet("""
            QPushButton {
                background: linear-gradient(45deg, #4ecdc4, #44a08d);
                color: white;
                border: none;
                border-radius: 10px;
                padding: 15px 30px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: linear-gradient(45deg, #44a08d, #3a8c78);
            }
        """)
        self.start_btn.clicked.connect(self.start_server)
        button_layout.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("🔴 停止服务器")
        self.stop_btn.setFont(QFont("Arial", 14))
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background: linear-gradient(45deg, #ff6b6b, #ee5a5a);
                color: white;
                border: none;
                border-radius: 10px;
                padding: 15px 30px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: linear-gradient(45deg, #ee5a5a, #d44a4a);
            }
        """)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_server)
        button_layout.addWidget(self.stop_btn)
        
        main_layout.addLayout(button_layout)
        
        # 状态栏
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage("就绪")
        
        # 初始化Flask线程
        self.flask_thread = None
        
        # 检查端口有效性
        self.port_input.textChanged.connect(self.validate_port)
        
    def validate_port(self, text):
        try:
            port = int(text)
            if 1024 <= port <= 65535:
                self.start_btn.setEnabled(True)
                return True
            else:
                self.start_btn.setEnabled(False)
                return False
        except ValueError:
            self.start_btn.setEnabled(False)
            return False
    
    def start_server(self):
        port_text = self.port_input.text()
        if not self.validate_port(port_text):
            QMessageBox.warning(self, "错误", "请输入有效的端口号 (1024-65535)")
            return
        
        port = int(port_text)
        
        # 禁用开始按钮，启用停止按钮
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.port_input.setEnabled(False)
        
        # 更新状态
        self.status_label.setText("🟡 正在启动服务器...")
        self.status_label.setStyleSheet("color: #ffd166;")
        self.statusBar.showMessage("正在启动服务器...")
        
        # 创建并启动Flask线程
        self.flask_thread = FlaskThread(port)
        self.flask_thread.server_started.connect(self.on_server_started)
        self.flask_thread.server_stopped.connect(self.on_server_stopped)
        self.flask_thread.start()
    
    def stop_server(self):
        if self.flask_thread and self.flask_thread.isRunning():
            self.status_label.setText("🟡 正在停止服务器...")
            self.status_label.setStyleSheet("color: #ffd166;")
            self.statusBar.showMessage("正在停止服务器...")
            
            # 停止服务器
            self.flask_thread.stop()
    
    def on_server_started(self, ip, port):
        # 更新UI
        self.status_label.setText("🟢 服务器运行中")
        self.status_label.setStyleSheet("color: #4ecdc4;")
        self.ip_label.setText(f"IP地址: {ip}")
        self.url_label.setText(f"访问地址: http://{ip}:{port}")
        self.statusBar.showMessage(f"服务器已启动，访问地址: http://{ip}:{port}")
        
        # 启用停止按钮
        self.stop_btn.setEnabled(True)
    
    def on_server_stopped(self):
        # 更新UI
        self.status_label.setText("🔴 服务器已停止")
        self.status_label.setStyleSheet("color: #ff6b6b;")
        self.ip_label.setText("IP地址: -")
        self.url_label.setText("访问地址: -")
        self.statusBar.showMessage("服务器已停止")
        
        # 重置按钮状态
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.port_input.setEnabled(True)
        
        # 清理线程
        self.flask_thread = None
    
    def closeEvent(self, event):
        # 确保在关闭窗口时停止服务器
        if self.flask_thread and self.flask_thread.isRunning():
            reply = QMessageBox.question(self, '确认退出',
                                        '服务器正在运行，确定要退出吗？',
                                        QMessageBox.Yes | QMessageBox.No,
                                        QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.stop_server()
                # 等待服务器停止
                QTimer.singleShot(1000, lambda: event.accept())
                return
            else:
                event.ignore()
        else:
            event.accept()

def run_gui():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')  # 使用现代风格
    
    # 设置应用程序样式
    app.setStyleSheet("""
        QMainWindow {
            background: linear-gradient(135deg, #f5f7fa 0%, #e4edf5 100%);
        }
        QGroupBox {
            border: 2px solid #667eea;
            border-radius: 10px;
            margin-top: 1ex;
            font-weight: bold;
            color: #667eea;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
        }
        QLabel {
            font-size: 14px;
            color: #333;
        }
        QLineEdit {
            padding: 8px;
            border: 2px solid #ddd;
            border-radius: 5px;
            font-size: 14px;
        }
        QLineEdit:focus {
            border-color: #667eea;
        }
    """)
    
    window = RemoteControlGUI()
    window.show()
    
    sys.exit(app.exec_())

def _deprecated():
    import textwrap
    print(textwrap.dedent("""\
        ============================================================
        [已弃用] main.py 是旧版单体入口（Flask + PyQt5 混写，存在
        无密码、路径穿越、shell=True RCE 等已知漏洞，仅作历史存档）。

        请改用重构后的安全版本 v1.4 (rc14 包)：
            python -m rc14.main                     # GUI 控制面板
            python -m rc14.main --headless \\
                --port 5000 --password s3cret       # 无 GUI，仅服务器
            python tests/test_rc14.py               # 运行自检
            build_rc14.bat                          # PyInstaller 一键打包
        ============================================================"""))
    raise SystemExit(1)


if __name__ == '__main__':
    _deprecated()
    run_gui()