
def _deprecated_entry():
    import textwrap, os
    print(textwrap.dedent("""\
        ============================================================
        [已弃用/存档] 本文件是历史版本，含已知漏洞（无密码、shell=True
        RCE、路径穿越等），仅供查阅，请勿直接运行。
        安全重构版入口：python -m rc14.main   （详见 README.md）
        ============================================================"""))
    raise SystemExit(1)


_deprecated_entry()  # guard FIRST: archived vulnerable server must never run

import os
import io
import base64
import threading
import socket
import sys
import time
import subprocess
import wave
import pyaudio
import cv2
import numpy as np
from flask import Flask, render_template_string, request, jsonify, Response, send_file
from PIL import ImageGrab
import pyautogui
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                           QHBoxLayout, QPushButton, QLabel, QLineEdit, 
                           QGroupBox, QFormLayout, QMessageBox, QStatusBar,
                            QComboBox, QSlider, QSpinBox, QCheckBox, QTextEdit,
                           QSystemTrayIcon, QAction, QMenu, QFileDialog)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtCore import pyqtSlot
from functools import wraps

app = Flask(__name__)
pyautogui.FAILSAFE = False

# ===== 安全加固 =====
# 认证密码：优先从环境变量读取，未设置时生成随机密码并打印
import secrets as _secrets
AUTH_TOKEN = os.environ.get("REMOTE_CONTROL_PASSWORD", "")
if not AUTH_TOKEN:
    AUTH_TOKEN = _secrets.token_urlsafe(16)
    print(f"[安全] 未设置 REMOTE_CONTROL_PASSWORD，已生成随机密码: {AUTH_TOKEN}")
    print(f"[安全] 请在请求头中携带 X-Auth-Token: {AUTH_TOKEN}")

# 上传目录的真实路径（用于路径遍历防护）
UPLOAD_REAL_DIR = os.path.realpath(Config.upload_dir)

def safe_join(base_dir, user_path):
    """安全拼接路径，防止路径遍历攻击。返回 None 表示路径越界。"""
    if not user_path:
        return None
    # 拒绝空字节和绝对路径
    if "\x00" in user_path:
        return None
    # 规范化路径
    candidate = os.path.realpath(os.path.join(base_dir, user_path))
    # 验证路径在允许目录内
    if not (candidate == base_dir or candidate.startswith(base_dir + os.sep)):
        return None
    return candidate

@app.before_request
def require_auth():
    """全局认证中间件：所有请求必须携带正确的 X-Auth-Token。"""
    # 预检请求放行
    if request.method == "OPTIONS":
        return None
    token = request.headers.get("X-Auth-Token", "")
    if not token or token != AUTH_TOKEN:
        return jsonify({"status": "error", "message": "未授权或密码错误"}), 401
    return None

# 线程安全的全局变量
screen_thread = None
flask_thread = None
server_running = False
camera_thread = None
microphone_thread = None
screen_thread_lock = threading.Lock()
server_running_lock = threading.Lock()

# 全局配置类
class Config:
    resolution = (800, 600)
    fps = 30
    jpeg_quality = 70
    aspect_ratio = '16:9'
    camera_enabled = True
    microphone_enabled = False
    audio_stream = None
    tray_icon = None
    upload_dir = "uploads"

os.makedirs(Config.upload_dir, exist_ok=True)

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
        self.resolution = Config.resolution
        self.fps = Config.fps
        self.jpeg_quality = Config.jpeg_quality
        self.frame_time = 1.0 / self.fps if self.fps > 0 else 0.01
     
    def update_settings(self, resolution=None, fps=None, quality=None):
        if resolution:
            self.resolution = resolution
        if fps:
            self.fps = fps
            self.frame_time = 1.0 / self.fps if self.fps > 0 else 0.01
        if quality:
            self.jpeg_quality = quality
    
    def run(self):
        last_capture_time = time.time()
        
        while self.running:
            current_time = time.time()
            if current_time - last_capture_time < self.frame_time:
                remaining_time = self.frame_time - (current_time - last_capture_time)
                if remaining_time > 0:
                    time.sleep(remaining_time)
                continue
            
            last_capture_time = current_time
            
            try:
                screenshot = ImageGrab.grab()
                img = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
                img = cv2.resize(img, self.resolution)
                _, buffer = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
                
                frame_bytes = buffer.tobytes()
                with self.lock:
                    self.last_frame = frame_bytes
            except Exception as e:
                print(f"屏幕捕获错误: {e}")
                time.sleep(0.1)
    
    def stop(self):
        self.running = False
    
    def get_frame(self):
        with self.lock:
            return self.last_frame if self.last_frame else b''

# 摄像头捕获线程
class CameraCaptureThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self.daemon = True
        self.running = True
        self.last_frame = None
        self.lock = threading.Lock()
        self.cap = None
    
    def run(self):
        try:
            self.cap = cv2.VideoCapture(0)
            if not self.cap.isOpened():
                print("摄像头无法打开")
                return
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            
            while self.running:
                ret, frame = self.cap.read()
                if ret:
                    _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    frame_bytes = buffer.tobytes()
                    
                    with self.lock:
                        self.last_frame = frame_bytes
                else:
                    time.sleep(0.1)
        except Exception as e:
            print(f"摄像头捕获错误: {e}")
        finally:
            if self.cap:
                self.cap.release()
    
    def stop(self): 
        self.running = False
    
    def get_frame(self):
        with self.lock:
            return self.last_frame if self.last_frame else b''

# 麦克风捕获线程
class MicrophoneCaptureThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self.daemon = True
        self.running = True
        self.audio_data = b""
        self.lock = threading.Lock()
        self.chunk = 1024
        self.format = pyaudio.paInt16
        self.channels = 2
        self.rate = 44100
    
    def run(self):
        try:
            p = pyaudio.PyAudio()
            stream = p.open(
                format=self.format,
                channels=self.channels,
                rate=self.rate,
                input=True,
                frames_per_buffer=self.chunk
            )
            
            while self.running:
                data = stream.read(self.chunk)
                with self.lock:
                    self.audio_data = data
        except Exception as e:
            print(f"麦克风捕获错误: {e}")
        finally:
            try:
                stream.stop_stream()
                stream.close()
                p.terminate()
            except:
                pass
    
    def stop(self):
        self.running = False
    
    def get_audio(self):
        with self.lock:
            return self.audio_data if self.audio_data else b''

# 修复后的HTML模板
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🖥️ 远程控制系统</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #1a1a2e;
            color: #eee;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        .header {
            background: #16213e;
            padding: 15px 20px;
            border-radius: 10px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header-info { color: #00d9ff; }
        .status { color: #00ff88; }
        .video-container {
            background: #000;
            border-radius: 10px;
            overflow: hidden;
            margin-bottom: 20px;
            position: relative;
            text-align: center;
        }
        .video-container img {
            width: 100%;
            height: auto;
            display: block;
        }
        .controls {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }
        .btn {
            background: #0f3460;
            color: #fff;
            border: none;
            padding: 15px 20px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.3s;
        }
        .btn:hover {
            background: #1a4a7a;
            transform: translateY(-2px);
        }
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.8);
            z-index: 1000;
        }
        .modal-content {
            background: #16213e;
            margin: 5% auto;
            padding: 30px;
            width: 80%;
            max-width: 700px;
            border-radius: 10px;
            position: relative;
        }
        .close {
            position: absolute;
            right: 20px;
            top: 20px;
            font-size: 28px;
            cursor: pointer;
            color: #ff6b6b;
        }
        .close:hover { color: #ff0000; }
        #fileDrop {
            border: 3px dashed #00d9ff;
            padding: 40px;
            text-align: center;
            margin: 20px 0;
            border-radius: 10px;
            cursor: pointer;
        }
        #fileDrop:hover { background: #0f3460; }
        .file-item {
            display: flex;
            justify-content: space-between;
            padding: 12px;
            border-bottom: 1px solid #0f3460;
        }
        .file-item:hover { background: #0f3460; }
        input[type="text"] {
            width: 100%;
            padding: 10px;
            margin: 10px 0;
            background: #0f3460;
            border: 1px solid #1a4a7a;
            color: #fff;
            border-radius: 5px;
        }
        #cmdOutput {
            background: #0a0a0a;
            padding: 15px;
            margin-top: 15px;
            max-height: 300px;
            overflow: auto;
            font-family: 'Courier New', monospace;
            border-radius: 5px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="header-info">
                IP: {{ ip }} | 端口: {{ port }}
            </div>
            <div class="status">
                状态: ✅ 在线 | 分辨率: {{ resolution }}
            </div>
        </div>
        
        <div class="video-container">
            <img id="videoFeed" src="/video_feed" alt="屏幕监控">
        </div>
        
        <div class="controls">
            <button class="btn" onclick="openModal('mouse')">🖱️ 鼠标控制</button>
            <button class="btn" onclick="openModal('keyboard')">⌨️ 键盘控制</button>
            <button class="btn" onclick="openModal('system')">⚙️ 系统控制</button>
            <button class="btn" onclick="toggleFullscreen()">⛶ 全屏模式</button>
            <button class="btn" onclick="openModal('cmd')">💻 CMD命令</button>
            <button class="btn" onclick="openModal('camera')">📷 摄像头</button>
            <button class="btn" onclick="toggleMic()">🎤 麦克风</button>
            <button class="btn" onclick="openModal('files')">📁 文件传输</button>
        </div>
        
        <div id="statusBar" style="text-align:center; color:#888; margin-top:10px;">就绪</div>
    </div>
    
    <!-- 模态框 -->
    <div id="modal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeModal()">×</span>
            <div id="modalBody"></div>
        </div>
    </div>

    <script>
        let fullscreen = false;
        
        function toggleFullscreen() {
            if (!document.fullscreenElement) {
                document.documentElement.requestFullscreen();
                fullscreen = true;
            } else {
                document.exitFullscreen();
                fullscreen = false;
            }
        }
        
        function openModal(type) {
            const body = document.getElementById('modalBody');
            document.getElementById('modal').style.display = 'block';
            
            if (type === 'mouse') {
                body.innerHTML = '<h3>🖱️ 鼠标控制</h3><p>直接在视频区域点击和移动即可控制鼠标</p>';
            } else if (type === 'keyboard') {
                body.innerHTML = `
                    <h3>⌨️ 键盘控制</h3>
                    <input type="text" id="keyInput" placeholder="输入按键或组合键（如：ctrl+c）">
                    <button class="btn" onclick="sendKey()" style="margin-top:10px;">发送</button>
                `;
            } else if (type === 'system') {
                body.innerHTML = `
                    <h3>⚙️ 系统控制</h3>
                    <button class="btn" onclick="sysAction('shutdown')" style="margin:5px;">关机</button>
                    <button class="btn" onclick="sysAction('restart')" style="margin:5px;">重启</button>
                    <button class="btn" onclick="sysAction('sleep')" style="margin:5px;">睡眠</button>
                `;
            } else if (type === 'cmd') {
                body.innerHTML = `
                    <h3>💻 CMD命令</h3>
                    <input type="text" id="cmdInput" placeholder="输入CMD命令">
                    <button class="btn" onclick="execCmd()" style="margin-top:10px;">执行</button>
                    <div id="cmdOutput"></div>
                `;
            } else if (type === 'camera') {
                body.innerHTML = '<h3>📷 摄像头</h3><img src="/camera_feed" style="width:100%; border-radius:5px;">';
            } else if (type === 'files') {
                body.innerHTML = `
                    <h3>📁 文件传输</h3>
                    <div id="fileDrop" onclick="document.getElementById('fileInput').click()">
                        📤 点击或拖拽文件到此处上传
                    </div>
                    <input type="file" id="fileInput" style="display:none" multiple>
                    <button class="btn" onclick="refreshFiles()" style="margin-top:10px;">🔄 刷新列表</button>
                    <div id="fileList"></div>
                `;
                setupFileDrop();
            }
        }
        
        function closeModal() {
            document.getElementById('modal').style.display = 'none';
        }
        
        function sendKey() {
            const key = document.getElementById('keyInput').value;
            if (key) {
                fetch('/keyboard', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({key: key})
                }).then(r => r.json()).then(d => {
                    if (d.status === 'success') {
                        document.getElementById('keyInput').value = '';
                    }
                });
            }
        }
        
        function sysAction(action) {
            if (confirm('确定要执行' + action + '操作吗？')) {
                fetch('/system', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({action: action})
                });
            }
        }
        
        function execCmd() {
            const cmd = document.getElementById('cmdInput').value;
            fetch('/execute_cmd', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({command: cmd})
            }).then(r => r.json()).then(d => {
                document.getElementById('cmdOutput').textContent = d.output;
            });
        }
        
        function toggleMic() {
            fetch('/toggle_microphone', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({enabled: true})
            }).then(r => r.json()).then(d => {
                alert('麦克风状态: ' + (d.enabled ? '开启' : '关闭'));
            });
        }
        
        function setupFileDrop() {
            const dropZone = document.getElementById('fileDrop');
            const fileInput = document.getElementById('fileInput');
            
            dropZone.addEventListener('dragover', (e) => {
                e.preventDefault();
                dropZone.style.background = '#0f3460';
            });
            
            dropZone.addEventListener('dragleave', () => {
                dropZone.style.background = '';
            });
            
            dropZone.addEventListener('drop', (e) => {
                e.preventDefault();
                dropZone.style.background = '';
                const files = e.dataTransfer.files;
                uploadFiles(files);
            });
            
            fileInput.addEventListener('change', (e) => {
                uploadFiles(e.target.files);
            });
        }
        
        function uploadFiles(files) {
            for (let file of files) {
                const formData = new FormData();
                formData.append('file', file);
                
                fetch('/upload_file', {
                    method: 'POST',
                    body: formData
                }).then(r => r.json()).then(d => {
                    if (d.success) {
                        alert('文件上传成功: ' + file.name);
                        refreshFiles();
                    } else {
                        alert('上传失败: ' + d.error);
                    }
                });
            }
        }
        
        function refreshFiles() {
            fetch('/list_files').then(r => r.json()).then(d => {
                const list = document.getElementById('fileList');
                if (d.success && d.files.length > 0) {
                    let html = '';
                    d.files.forEach(f => {
                        html += `
                            <div class="file-item">
                                <span>${f.name} (${(f.size/1024).toFixed(1)} KB)</span>
                                <div>
                                    <button class="btn" onclick="downloadFile('${f.name}')" style="padding:5px 10px; margin-right:5px;">下载</button>
                                    <button class="btn" onclick="deleteFile('${f.name}')" style="padding:5px 10px; background:#ff6b6b;">删除</button>
                                </div>
                            </div>
                        `;
                    });
                    list.innerHTML = html;
                } else {
                    list.innerHTML = '<p style="text-align:center; color:#888;">暂无文件</p>';
                }
            });
        }
        
        function downloadFile(filename) {
            window.location.href = '/download_file/' + filename;
        }
        
        function deleteFile(filename) {
            if (confirm('确定删除文件: ' + filename + ' ?')) {
                fetch('/delete_file/' + filename, {method: 'DELETE'})
                    .then(r => r.json())
                    .then(d => {
                        if (d.success) refreshFiles();
                        else alert('删除失败');
                    });
            }
        }
        
        window.onclick = function(event) {
            const modal = document.getElementById('modal');
            if (event.target == modal) {
                closeModal();
            }
        }
    </script>
</body>
</html>
'''

# 添加CORS支持（收紧：不再使用通配符，回显请求来源）
@app.after_request
def after_request(response):
    origin = request.headers.get("Origin", "")
    if origin:
        response.headers.add('Access-Control-Allow-Origin', origin)
        response.headers.add('Access-Control-Allow-Credentials', 'true')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,X-Auth-Token')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

@app.route('/')
def index():
    local_ip = get_local_ip()
    resolution_str = f"{Config.resolution[0]}x{Config.resolution[1]}"
    return render_template_string(HTML_TEMPLATE, 
                                ip=local_ip, 
                                port=5000,
                                resolution=resolution_str)

# 修复视频流路由
def generate_video_feed():
    global screen_thread
    while True:
        if screen_thread:
            frame = screen_thread.get_frame()
            if frame:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        time.sleep(0.033)  # 约30fps

@app.route('/video_feed')
def video_feed():
    return Response(generate_video_feed(), 
                   mimetype='multipart/x-mixed-replace; boundary=frame',
                   headers={'Cache-Control': 'no-cache'})

def generate_camera_feed():
    global camera_thread
    while True:
        if camera_thread and Config.camera_enabled:
            frame = camera_thread.get_frame()
            if frame:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        time.sleep(0.033)

@app.route('/camera_feed')
def camera_feed():
    return Response(generate_camera_feed(),
                   mimetype='multipart/x-mixed-replace; boundary=frame',
                   headers={'Cache-Control': 'no-cache'})

@app.route('/upload_file', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '没有文件被上传'})
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': '没有选择文件'})
        
        # 安全检查：防止路径遍历，只取文件名部分
        safe_name = os.path.basename(file.filename)
        filepath = safe_join(UPLOAD_REAL_DIR, safe_name)
        if filepath is None:
            return jsonify({'success': False, 'error': '文件名不合法或路径越界'}), 400
        
        file.save(filepath)
        
        return jsonify({'success': True, 'message': '文件上传成功'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/list_files')
def list_files():
    try:
        files = []
        for filename in os.listdir(Config.upload_dir):
            filepath = os.path.join(Config.upload_dir, filename)
            if os.path.isfile(filepath):
                files.append({
                    'name': filename,
                    'size': os.path.getsize(filepath),
                    'modified': os.path.getmtime(filepath)
                })
        
        files.sort(key=lambda x: x['modified'], reverse=True)
        
        return jsonify({'success': True, 'files': files})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/download_file/<path:filename>')
def download_file(filename):
    try:
        # 安全检查：防止路径遍历
        safe_name = os.path.basename(filename)
        filepath = safe_join(UPLOAD_REAL_DIR, safe_name)
        if filepath is None or not os.path.exists(filepath):
            return '文件不存在或路径不合法', 404
        return send_file(filepath, as_attachment=True)
    except Exception as e:
        return f'下载失败: {str(e)}', 500

@app.route('/delete_file/<path:filename>', methods=['DELETE'])
def delete_file(filename):
    try:
        # 安全检查：防止路径遍历
        safe_name = os.path.basename(filename)
        filepath = safe_join(UPLOAD_REAL_DIR, safe_name)
        if filepath is None:
            return jsonify({'success': False, 'error': '路径不合法'}), 400
        if os.path.exists(filepath):
            os.remove(filepath)
            return jsonify({'success': True, 'message': '文件删除成功'})
        else:
            return jsonify({'success': False, 'error': '文件不存在'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/execute_cmd', methods=['POST'])
def execute_cmd():
    try:
        data = request.json
        if not data or 'command' not in data:
            return jsonify({'status': 'error', 'output': 'Invalid request'}), 400
            
        command = data['command']
        
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )
            output = result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            output = "命令执行超时"
        except Exception as e:
            output = f"命令执行失败: {str(e)}"
        
        return jsonify({'status': 'success', 'output': output})
    except Exception as e:
        return jsonify({'status': 'error', 'output': f'内部错误: {str(e)}'}), 500

@app.route('/toggle_microphone', methods=['POST'])
def toggle_microphone():
    try:
        data = request.json
        if not data or 'enabled' not in data:
            return jsonify({'status': 'error', 'message': 'Invalid request'}), 400
            
        enabled = data['enabled']
        
        global microphone_thread
        if enabled and not Config.microphone_enabled:
            with screen_thread_lock:
                if microphone_thread is None:
                    microphone_thread = MicrophoneCaptureThread()
                    microphone_thread.start()
            Config.microphone_enabled = True
        elif not enabled and Config.microphone_enabled:
            with screen_thread_lock:
                if microphone_thread:
                    microphone_thread.stop()
                    microphone_thread = None
            Config.microphone_enabled = False
        
        return jsonify({'enabled': Config.microphone_enabled})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/toggle_camera', methods=['POST'])
def toggle_camera():
    try:
        data = request.json
        if not data or 'enabled' not in data:
            return jsonify({'status': 'error', 'message': 'Invalid request'}), 400
            
        enabled = data['enabled']
        
        global camera_thread
        if enabled and not Config.camera_enabled:
            with screen_thread_lock:
                if camera_thread is None:
                    camera_thread = CameraCaptureThread()
                    camera_thread.start()
            Config.camera_enabled = True
        elif not enabled and Config.camera_enabled:
            with screen_thread_lock:
                if camera_thread:
                    camera_thread.stop()
                    camera_thread = None
            Config.camera_enabled = False
        
        return jsonify({'enabled': Config.camera_enabled})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/mouse_move', methods=['POST'])
def mouse_move():
    try:
        data = request.json
        if not data or 'x' not in data or 'y' not in data:
            return jsonify({'status': 'error', 'message': 'Invalid request'}), 400
            
        x = data['x']
        y = data['y']
        screen_width = data['screen_width']
        screen_height = data['screen_height']
        fullscreen = data.get('fullscreen', False)
        
        screen_size = pyautogui.size()
        
        if fullscreen:
            actual_x = x * (screen_size.width / screen_width)
            actual_y = y * (screen_size.height / screen_height)
        else:
            aspect_ratio = screen_width / screen_height
            screen_aspect = screen_size.width / screen_size.height
            
            if aspect_ratio > screen_aspect:
                new_height = screen_size.height
                new_width = screen_size.height * aspect_ratio
                offset_x = (screen_size.width - new_width) / 2
                offset_y = 0
                actual_x = offset_x + (x * (new_width / screen_width))
                actual_y = y * (new_height / screen_height)
            else:
                new_width = screen_size.width
                new_height = screen_size.width / aspect_ratio
                offset_x = 0
                offset_y = (screen_size.height - new_height) / 2
                actual_x = x * (new_width / screen_width)
                actual_y = offset_y + (y * (new_height / screen_height))
        
        actual_x = max(0, min(actual_x, screen_size.width - 1))
        actual_y = max(0, min(actual_y, screen_size.height - 1))
        
        pyautogui.moveTo(actual_x, actual_y)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/mouse_click', methods=['POST'])
def mouse_click():
    try:
        data = request.json
        if not data or 'x' not in data or 'y' not in data:
            return jsonify({'status': 'error', 'message': 'Invalid request'}), 400
            
        x = data['x']
        y = data['y']
        screen_width = data['screen_width']
        screen_height = data['screen_height']
        fullscreen = data.get('fullscreen', False)
        
        screen_size = pyautogui.size()
        
        if fullscreen:
            actual_x = x * (screen_size.width / screen_width)
            actual_y = y * (screen_size.height / screen_height)
        else:
            aspect_ratio = screen_width / screen_height
            screen_aspect = screen_size.width / screen_size.height
            
            if aspect_ratio > screen_aspect:
                new_height = screen_size.height
                new_width = screen_size.height * aspect_ratio
                offset_x = (screen_size.width - new_width) / 2
                actual_x = offset_x + (x * (new_width / screen_width))
                actual_y = y * (new_height / screen_height)
            else:
                new_width = screen_size.width
                new_height = screen_size.width / aspect_ratio
                offset_y = (screen_size.height - new_height) / 2
                actual_x = x * (new_width / screen_width)
                actual_y = offset_y + (y * (new_height / screen_height))
        
        actual_x = max(0, min(actual_x, screen_size.width - 1))
        actual_y = max(0, min(actual_y, screen_size.height - 1))
        
        pyautogui.click(actual_x, actual_y)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/mouse_right_click', methods=['POST'])
def mouse_right_click():
    try:
        data = request.json
        if not data or 'x' not in data or 'y' not in data:
            return jsonify({'status': 'error', 'message': 'Invalid request'}), 400
            
        x = data['x']
        y = data['y']
        screen_width = data['screen_width']
        screen_height = data['screen_height']
        fullscreen = data.get('fullscreen', False)
        
        screen_size = pyautogui.size()
        
        if fullscreen:
            actual_x = x * (screen_size.width / screen_width)
            actual_y = y * (screen_size.height / screen_height)
        else:
            aspect_ratio = screen_width / screen_height
            screen_aspect = screen_size.width / screen_size.height
            
            if aspect_ratio > screen_aspect:
                new_height = screen_size.height
                new_width = screen_size.height * aspect_ratio
                offset_x = (screen_size.width - new_width) / 2
                actual_x = offset_x + (x * (new_width / screen_width))
                actual_y = y * (new_height / screen_height)
            else:
                new_width = screen_size.width
                new_height = screen_size.width / aspect_ratio
                offset_y = (screen_size.height - new_height) / 2
                actual_x = x * (new_width / screen_width)
                actual_y = offset_y + (y * (new_height / screen_height))
        
        actual_x = max(0, min(actual_x, screen_size.width - 1))
        actual_y = max(0, min(actual_y, screen_size.height - 1))
        
        pyautogui.rightClick(actual_x, actual_y)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/mouse_scroll', methods=['POST'])
def mouse_scroll():
    try:
        data = request.json
        if not data or 'delta' not in data:
            return jsonify({'status': 'error', 'message': 'Invalid request'}), 400
            
        delta = data['delta']
        pyautogui.scroll(int(-delta/50))
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/keyboard', methods=['POST'])
def keyboard():
    try:
        data = request.json
        if not data or 'key' not in data:
            return jsonify({'status': 'error', 'message': 'Invalid request'}), 400
            
        key = data['key']
        
        special_keys = ['ctrl', 'alt', 'shift', 'win', 'cmd', 'esc', 'enter', 'space', 'tab',
                       'backspace', 'delete', 'home', 'end', 'pageup', 'pagedown',
                       'up', 'down', 'left', 'right']
        
        if key.lower() in special_keys:
            pyautogui.press(key.lower())
        elif len(key) == 1:
            pyautogui.write(key)
        else:
            if '+' in key:
                keys = [k.strip() for k in key.split('+')]
                pyautogui.hotkey(*keys)
            else:
                pyautogui.write(key)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/system', methods=['POST'])
def system():
    try:
        data = request.json
        if not data or 'action' not in data:
            return jsonify({'status': 'error', 'message': 'Invalid request'}), 400
            
        action = data['action'].lower()
        
        if action == 'shutdown':
            if os.name == 'nt':
                os.system('shutdown /s /t 0')
            else:
                os.system('shutdown now')
        elif action == 'restart':
            if os.name == 'nt':
                os.system('shutdown /r /t 0')
            else:
                os.system('sudo reboot')
        elif action == 'sleep':
            if os.name == 'nt':
                os.system('rundll32.exe powrprof.dll,SetSuspendState 0,1,0')
            elif sys.platform.startswith('darwin'):
                os.system('pmset displaysleepnow')
            else:
                os.system('systemctl suspend')
        else:
            return jsonify({'status': 'error', 'message': 'Unknown command'}), 400
            
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

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
        global screen_thread, server_running, camera_thread, microphone_thread
        try:
            with screen_thread_lock:
                screen_thread = ScreenCaptureThread()
                screen_thread.start()
                print("✅ 屏幕捕获线程已启动")
            
            if Config.camera_enabled:
                with screen_thread_lock:
                    camera_thread = CameraCaptureThread()
                    camera_thread.start()
                    print("✅ 摄像头线程已启动")
            
            if Config.microphone_enabled:
                with screen_thread_lock:
                    microphone_thread = MicrophoneCaptureThread()
                    microphone_thread.start()
                    print("✅ 麦克风线程已启动")
            
            local_ip = get_local_ip()
            self.server_started.emit(local_ip, self.port)
            
            self.running = True
            with server_running_lock:
                server_running = True
            
            print(f"🌐 Flask服务器启动在 http://{local_ip}:{self.port}")
            app.run(host='0.0.0.0', port=self.port, threaded=True, debug=False)
            
        except Exception as e:
            print(f"服务器启动错误: {e}")
        finally:
            with server_running_lock:
                server_running = False
    
    def stop(self):
        global screen_thread, camera_thread, microphone_thread
        with screen_thread_lock:
            if screen_thread:
                screen_thread.stop()
                screen_thread = None
            if camera_thread:
                camera_thread.stop()
                camera_thread = None
            if microphone_thread:
                microphone_thread.stop()
                microphone_thread = None
        
        if self.running:
            self.running = False
            self.server_stopped.emit()

# 系统托盘图标类
class SystemTrayIcon(QSystemTrayIcon):
    def __init__(self, icon_path, parent=None):
        super().__init__(parent)
        self.setIcon(QIcon(icon_path))
        self.setVisible(True)
        
        menu = QMenu(parent)
        
        show_action = QAction("显示主窗口", parent)
        show_action.triggered.connect(self.show_main_window)
        menu.addAction(show_action)
        
        minimize_action = QAction("最小化到托盘", parent)
        minimize_action.triggered.connect(self.minimize_to_tray)
        menu.addAction(minimize_action)
        
        start_action = QAction("启动服务", parent)
        start_action.triggered.connect(self.start_service)
        menu.addAction(start_action)
        
        stop_action = QAction("停止服务", parent)
        stop_action.triggered.connect(self.stop_service)
        menu.addAction(stop_action)
        
        menu.addSeparator()
        
        exit_action = QAction("退出程序", parent)
        exit_action.triggered.connect(self.exit_program)
        menu.addAction(exit_action)
        
        self.setContextMenu(menu)
        self.activated.connect(self.on_tray_icon_activated)
        
        self.main_window = parent
    
    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_main_window()
    
    def show_main_window(self):
        if self.main_window:
            self.main_window.showNormal()
            self.main_window.raise_()
            self.main_window.activateWindow()
    
    def minimize_to_tray(self):
        if self.main_window:
            self.main_window.hide()
    
    def start_service(self):
        if self.main_window:
            self.main_window.start_server_from_tray()
    
    def stop_service(self):
        if self.main_window:
            self.main_window.stop_server_from_tray()
    
    def exit_program(self):
        if self.main_window:
            self.main_window.close_from_tray()

# PyQt5主窗口
class RemoteControlGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("远程控制被控端")
        self.setMinimumSize(800, 700)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(30, 30, 30, 30)
        
        title_label = QLabel("🖥️ 远程控制被控端")
        title_label.setFont(QFont("Arial", 24, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("color: #667eea; margin: 10px 0;")
        main_layout.addWidget(title_label)
        
        config_group = QGroupBox("服务器配置")
        config_group.setStyleSheet("""
            QGroupBox {
                background: #2d2d2d;
                border: 2px solid #667eea;
                border-radius: 10px;
                margin-top: 1ex;
                color: #e0e0e0;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                background: #667eea;
                color: white;
                border-radius: 5px;
            }
        """)
        config_layout = QFormLayout()
        
        self.port_input = QLineEdit("5000")
        self.port_input.setStyleSheet("""
            QLineEdit {
                padding: 8px;
                border: 2px solid #444;
                border-radius: 5px;
                background: #333;
                color: white;
                font-size: 14px;
            }
            QLineEdit:focus {
                border-color: #667eea;
                background: #444;
            }
        """)
        self.port_input.setPlaceholderText("请输入端口号 (1024-65535)")
        config_layout.addRow("端口号:", self.port_input)
        
        config_group.setLayout(config_layout)
        main_layout.addWidget(config_group)
        
        video_group = QGroupBox("视频设置")
        video_group.setStyleSheet("""
            QGroupBox {
                background: #2d2d2d;
                border: 2px solid #667eea;
                border-radius: 10px;
                margin-top: 1ex;
                color: #e0e0e0;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                background: #667eea;
                color: white;
                border-radius: 5px;
            }
        """)
        video_layout = QFormLayout()
        
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems([
            '640x480 (VGA)',
            '800x600 (SVGA)',
            '1024x768 (XGA)',
            '1280x720 (HD)',
            '1280x1024 (SXGA)',
            '1366x768 (WXGA)',
            '1600x900 (HD+)',
            '1920x1080 (Full HD)',
            '2560x1440 (QHD)',
            '3840x2160 (4K UHD)'
        ])
        self.resolution_combo.setCurrentIndex(1)
        self.resolution_combo.setStyleSheet("""
            QComboBox {
                padding: 5px;
                border: 2px solid #444;
                border-radius: 5px;
                background: #333;
                color: white;
            }
        """)
        video_layout.addRow("分辨率:", self.resolution_combo)
        
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(['15 FPS', '30 FPS', '45 FPS', '60 FPS'])
        self.fps_combo.setCurrentIndex(1)
        self.fps_combo.setStyleSheet("""
            QComboBox {
                padding: 5px;
                border: 2px solid #444;
                border-radius: 5px;
                background: #333;
                color: white;
            }
        """)
        video_layout.addRow("帧率:", self.fps_combo)
        
        quality_layout = QHBoxLayout()
        self.quality_slider = QSlider(Qt.Horizontal)
        self.quality_slider.setRange(10, 100)
        self.quality_slider.setValue(70)
        self.quality_label = QLabel('70%')
        
        self.quality_slider.valueChanged.connect(lambda v: self.quality_label.setText(f'{v}%'))
        
        quality_layout.addWidget(self.quality_slider)
        quality_layout.addWidget(self.quality_label)
        video_layout.addRow("画质:", quality_layout)
        
        video_group.setLayout(video_layout)
        main_layout.addWidget(video_group)
        
        features_group = QGroupBox("功能开关")
        features_group.setStyleSheet("""
            QGroupBox {
                background: #2d2d2d;
                border: 2px solid #667eea;
                border-radius: 10px;
                margin-top: 1ex;
                color: #e0e0e0;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                background: #667eea;
                color: white;
                border-radius: 5px;
            }
        """)
        features_layout = QHBoxLayout()
        
        self.camera_checkbox = QCheckBox("启用摄像头")
        self.camera_checkbox.setStyleSheet("""
            QCheckBox {
                color: #e0e0e0;
                font-size: 14px;
            }
        """)
        self.camera_checkbox.setChecked(True)
        features_layout.addWidget(self.camera_checkbox)
        
        self.mic_checkbox = QCheckBox("启用麦克风")
        self.mic_checkbox.setStyleSheet("""
            QCheckBox {
                color: #e0e0e0;
                font-size: 14px;
            }
        """)
        features_layout.addWidget(self.mic_checkbox)
        
        features_group.setLayout(features_layout)
        main_layout.addWidget(features_group)
        
        status_group = QGroupBox("服务器状态")
        status_group.setStyleSheet("""
            QGroupBox {
                background: #2d2d2d;
                border: 2px solid #667eea;
                border-radius: 10px;
                margin-top: 1ex;
                color: #e0e0e0;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                background: #667eea;
                color: white;
                border-radius: 5px;
            }
        """)
        status_layout = QVBoxLayout()
        
        self.status_label = QLabel("🔴 服务器未运行")
        self.status_label.setFont(QFont("Arial", 14))
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: #ff6b6b; padding: 10px;")
        status_layout.addWidget(self.status_label)
        
        self.ip_label = QLabel("IP地址: -")
        self.ip_label.setFont(QFont("Arial", 12))
        self.ip_label.setStyleSheet("color: #aaa;")
        status_layout.addWidget(self.ip_label)
        
        self.url_label = QLabel("访问地址: -")
        self.url_label.setFont(QFont("Arial", 12))
        self.url_label.setStyleSheet("color: #44a08d; font-weight: bold;")
        status_layout.addWidget(self.url_label)
        
        status_group.setLayout(status_layout)
        main_layout.addWidget(status_group)
        
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
            QPushButton:disabled {
                background: #555;
                color: #888;
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
            QPushButton:disabled {
                background: #555;
                color: #888;
            }
        """)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_server)
        button_layout.addWidget(self.stop_btn)
        
        self.minimize_btn = QPushButton("🔽 最小化到托盘")
        self.minimize_btn.setFont(QFont("Arial", 14))
        self.minimize_btn.setStyleSheet("""
            QPushButton {
                background: linear-gradient(45deg, #a1c4fd, #c2e9fb);
                color: #333;
                border: none;
                border-radius: 10px;
                padding: 15px 30px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: linear-gradient(45deg, #9bbefc, #b8d8f8);
            }
        """)
        self.minimize_btn.clicked.connect(self.minimize_to_tray)
        button_layout.addWidget(self.minimize_btn)
        
        main_layout.addLayout(button_layout)
        
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.setStyleSheet("""
            QStatusBar {
                background: #1a1a1a;
                color: #aaa;
                border-top: 1px solid #333;
            }
        """)
        self.statusBar.showMessage("就绪")
        
        self.flask_thread = None
        
        self.port_input.textChanged.connect(self.validate_port)
        
        self.setStyleSheet("""
            QMainWindow {
                background: linear-gradient(135deg, #0d0d0d 0%, #1a1a1a 100%);
            }
            QWidget {
                background: transparent;
            }
        """)
        
        try:
            self.tray_icon = SystemTrayIcon("app.ico", self)
            Config.tray_icon = self.tray_icon
        except Exception as e:
            print(f"托盘图标初始化失败: {e}")
            self.tray_icon = None
        
        QTimer.singleShot(100, self.hide_to_tray)
        QTimer.singleShot(5000, self.auto_start_server)
    
    def auto_start_server(self):
        print("5秒延迟结束，自动启动服务器...")
        self.start_server()
    
    def minimize_to_tray(self):
        self.hide()
        if self.tray_icon:
            self.tray_icon.showMessage("远程控制", "程序已最小化到托盘", QSystemTrayIcon.Information, 2000)
    
    def hide_to_tray(self):
        self.hide()
        if self.tray_icon:
            self.tray_icon.showMessage("远程控制", "程序已启动并隐藏到托盘", QSystemTrayIcon.Information, 2000)
    
    def validate_port(self, text):
        try:
            port = int(text)
            if 1024 <= port <= 65535:
                self.port_input.setStyleSheet("QLineEdit{padding:8px;border:2px solid #4ecdc4;border-radius:5px;background:#333;color:white;font-size:14px;}")
            else:
                self.port_input.setStyleSheet("QLineEdit{padding:8px;border:2px solid #ff6b6b;border-radius:5px;background:#333;color:white;font-size:14px;}")
        except:
            self.port_input.setStyleSheet("QLineEdit{padding:8px;border:2px solid #ff6b6b;border-radius:5px;background:#333;color:white;font-size:14px;}")
    
    def start_server(self):
        try:
            port = int(self.port_input.text())
            if not (1024 <= port <= 65535):
                raise ValueError("端口范围无效")
        except:
            QMessageBox.warning(self, "错误", "请输入有效的端口号 (1024-65535)")
            return
        
        self.flask_thread = FlaskThread(port)
        self.flask_thread.server_started.connect(self.on_server_started)
        self.flask_thread.server_stopped.connect(self.on_server_stopped)
        self.flask_thread.start()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
    
    def stop_server(self):
        if self.flask_thread:
            self.flask_thread.stop()
            self.flask_thread.wait()
    
    def start_server_from_tray(self):
        self.start_server()
    
    def stop_server_from_tray(self):
        self.stop_server()
    
    def close_from_tray(self):
        self.stop_server()
        self.close()
    
    @pyqtSlot(str, int)
    def on_server_started(self, ip, port):
        self.status_label.setText("🟢 服务器运行中")
        self.status_label.setStyleSheet("color: #4ecdc4; padding: 10px;")
        self.ip_label.setText(f"IP地址: {ip}")
        self.url_label.setText(f"访问地址: http://{ip}:{port}")
        self.statusBar.showMessage(f"服务器已启动: {ip}:{port}")
    
    @pyqtSlot()
    def on_server_stopped(self):
        self.status_label.setText("🔴 服务器已停止")
        self.status_label.setStyleSheet("color: #ff6b6b; padding: 10px;")
        self.ip_label.setText("IP地址: -")
        self.url_label.setText("访问地址: -")
        self.statusBar.showMessage("服务器已停止")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)


if __name__ == "__main__":
    app_qt = QApplication(sys.argv)
    app_qt.setQuitOnLastWindowClosed(False)
    gui = RemoteControlGUI()
    sys.exit(app_qt.exec_())