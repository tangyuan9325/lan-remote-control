
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
from flask import Flask, render_template_string, request, jsonify, Response
from PIL import ImageGrab
import pyautogui
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                           QHBoxLayout, QPushButton, QLabel, QLineEdit, 
                           QGroupBox, QFormLayout, QMessageBox, QStatusBar,
                           QComboBox, QSlider, QSpinBox, QCheckBox, QTextEdit,
                           QSystemTrayIcon, QAction, QMenu)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtCore import pyqtSlot

app = Flask(__name__)
pyautogui.FAILSAFE = False  # 禁用鼠标移动到左上角的fail-safe

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
    resolution = (800, 600)  # 默认分辨率
    fps = 30                 # 默认帧率
    jpeg_quality = 70        # 默认JPEG质量
    aspect_ratio = '16:9'    # 默认宽高比
    camera_enabled = True    # 默认开启摄像头
    microphone_enabled = False  # 麦克风状态
    audio_stream = None      # 音频流
    tray_icon = None         # 托盘图标引用

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
                # 睡眠剩余时间
                remaining_time = self.frame_time - (current_time - last_capture_time)
                if remaining_time > 0:
                    time.sleep(remaining_time)
                continue
            
            last_capture_time = current_time
            
            try:
                # 使用PIL捕获屏幕
                screenshot = ImageGrab.grab()
                # 转换为OpenCV格式
                img = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
                # 调整大小以提高传输效率
                img = cv2.resize(img, self.resolution)
                # 转换为JPEG格式
                _, buffer = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
                
                frame_bytes = buffer.tobytes()
                with self.lock:
                    self.last_frame = frame_bytes
            except Exception as e:
                print(f"屏幕捕获错误: {e}")
                time.sleep(0.1)  # 防止异常时CPU占用过高
    
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
            self.cap = cv2.VideoCapture(0)  # 打开默认摄像头
            if not self.cap.isOpened():
                print("摄像头无法打开")
                return
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            
            while self.running:
                ret, frame = self.cap.read()
                if ret:
                    # 转换为JPEG格式
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

# HTML模板 - 修复CMD命令和摄像头按钮功能
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
            background: linear-gradient(135deg, #1a1a1a 0%, #0d0d0d 100%);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            color: #e0e0e0;
        }
        .container {
            background: #2d2d2d;
            border-radius: 15px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.5);
            padding: 30px;
            width: 90%;
            max-width: 1200px;
            margin-top: 20px;
            border: 1px solid #444;
        }
        h1 {
            color: #fff;
            text-align: center;
            margin-bottom: 30px;
            font-size: 2.5em;
            background: linear-gradient(45deg, #667eea, #764ba2);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-shadow: 0 2px 4px rgba(0,0,0,0.3);
        }
        .screen-container {
            position: relative;
            border: 2px solid #667eea;
            border-radius: 10px;
            overflow: hidden;
            margin: 20px 0;
            box-shadow: 0 10px 30px rgba(0,0,0,0.4);
            background: #000;
            min-height: 400px;
        }
        #screen {
            width: 100%;
            display: block;
            cursor: crosshair;
        }
        .controls {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 15px;
            margin-top: 20px;
        }
        .control-btn {
            padding: 15px;
            border: none;
            border-radius: 10px;
            color: white;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
            transition: all 0.3s ease;
            box-shadow: 0 5px 15px rgba(0,0,0,0.3);
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
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
        .fullscreen-btn {
            background: linear-gradient(45deg, #6a11cb, #2575fc);
        }
        .cmd-btn {
            background: linear-gradient(45deg, #a8edea, #fed6e3);
            color: #333;
        }
        .camera-btn {
            background: linear-gradient(45deg, #ff9a9e, #fecfef);
            color: #333;
        }
        .mic-btn {
            background: linear-gradient(45deg, #a1c4fd, #c2e9fb);
            color: #333;
        }
        .control-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(0,0,0,0.4);
        }
        .status {
            text-align: center;
            margin-top: 20px;
            font-size: 1.1em;
            color: #aaa;
        }
        .connection-info {
            background: #333;
            padding: 15px;
            border-radius: 10px;
            margin: 20px 0;
            text-align: center;
            border: 1px solid #444;
            display: flex;
            justify-content: space-around;
            flex-wrap: wrap;
        }
        .info-item {
            margin: 5px 10px;
            font-weight: bold;
        }
        .fullscreen-container {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: #000;
            z-index: 1000;
            display: none;
            align-items: center;
            justify-content: center;
            touch-action: none;
        }
        .fullscreen-screen {
            max-width: 100%;
            max-height: 100%;
            cursor: crosshair;
        }
        .exit-fullscreen {
            position: absolute;
            top: 20px;
            right: 20px;
            background: rgba(255, 0, 0, 0.8);
            color: white;
            border: none;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            font-size: 20px;
            cursor: pointer;
            z-index: 1001;
            box-shadow: 0 2px 10px rgba(0,0,0,0.3);
        }
        .exit-fullscreen:hover {
            background: rgba(255, 0, 0, 1);
            transform: scale(1.1);
        }
        .cmd-container {
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: #2d2d2d;
            border-radius: 10px;
            padding: 20px;
            z-index: 2000;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
            display: none;
            width: 80%;
            max-width: 600px;
        }
        .cmd-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        .cmd-title {
            color: #667eea;
            font-size: 1.5em;
            font-weight: bold;
        }
        .cmd-close {
            background: #ff6b6b;
            border: none;
            border-radius: 50%;
            width: 30px;
            height: 30px;
            color: white;
            font-size: 18px;
            cursor: pointer;
        }
        .cmd-input {
            width: 100%;
            padding: 10px;
            background: #333;
            color: #e0e0e0;
            border: 1px solid #444;
            border-radius: 5px;
            font-family: monospace;
            font-size: 14px;
        }
        .cmd-output {
            width: 100%;
            height: 200px;
            padding: 10px;
            background: #000;
            color: #4ecdc4;
            border: 1px solid #444;
            border-radius: 5px;
            font-family: monospace;
            font-size: 14px;
            margin-top: 10px;
            overflow-y: auto;
        }
        .camera-container {
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: #2d2d2d;
            border-radius: 10px;
            padding: 20px;
            z-index: 2000;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
            display: none;
            width: 80%;
            max-width: 800px;
        }
        .camera-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        .camera-title {
            color: #667eea;
            font-size: 1.5em;
            font-weight: bold;
        }
        .camera-close {
            background: #ff6b6b;
            border: none;
            border-radius: 50%;
            width: 30px;
            height: 30px;
            color: white;
            font-size: 18px;
            cursor: pointer;
        }
        #camera-feed {
            width: 100%;
            display: block;
        }
        @media (max-width: 768px) {
            .controls {
                grid-template-columns: repeat(2, 1fr);
            }
            .container {
                padding: 20px;
                margin: 10px;
            }
        }
        @media (max-width: 480px) {
            .controls {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <h1>🖥️ 远程控制系统</h1>
    <div class="container">
        <div class="connection-info">
            <div class="info-item">IP: <strong>{{ ip }}</strong></div>
            <div class="info-item">端口: <strong>{{ port }}</strong></div>
            <div class="info-item">状态: <span id="connection-status" style="color: #4ecdc4;">✅ 在线</span></div>
            <div class="info-item">分辨率: <span id="resolution-info">{{ resolution }}</span></div>
        </div>
        
        <div class="screen-container">
            <img id="screen" src="/video_feed" alt="远程屏幕" onerror="handleImageError(this)">
        </div>
        
        <div class="controls">
            <button class="control-btn mouse-btn" onclick="showMouseControls()">🖱️ 鼠标控制</button>
            <button class="control-btn keyboard-btn" onclick="showKeyboardControls()">⌨️ 键盘控制</button>
            <button class="control-btn system-btn" onclick="showSystemControls()">⚙️ 系统控制</button>
            <button class="control-btn fullscreen-btn" onclick="toggleFullscreen()">⛶ 全屏模式</button>
            <button class="control-btn cmd-btn" onclick="toggleCmdPanel()">💻 CMD命令</button>
            <button class="control-btn camera-btn" onclick="toggleCameraPanel()">📷 摄像头</button>
            <button class="control-btn mic-btn" onclick="toggleMicrophone()">🎤 麦克风</button>
        </div>
        
        <div class="status" id="status">就绪</div>
    </div>

    <div class="fullscreen-container" id="fullscreen-container">
        <button class="exit-fullscreen" onclick="exitFullscreen()">×</button>
        <img id="fullscreen-screen" class="fullscreen-screen" src="/video_feed" alt="全屏远程屏幕" onerror="handleImageError(this)">
    </div>

    <!-- CMD面板 -->
    <div class="cmd-container" id="cmd-container">
        <div class="cmd-header">
            <div class="cmd-title">💻 CMD命令</div>
            <button class="cmd-close" onclick="closeCmdPanel()">×</button>
        </div>
        <input type="text" class="cmd-input" id="cmd-input" placeholder="输入CMD命令..." onkeypress="handleCmdKeyPress(event)">
        <div class="cmd-output" id="cmd-output"></div>
    </div>

    <!-- 摄像头面板 -->
    <div class="camera-container" id="camera-container">
        <div class="camera-header">
            <div class="camera-title">📷 摄像头</div>
            <button class="camera-close" onclick="closeCameraPanel()">×</button>
        </div>
        <img id="camera-feed" src="/camera_feed" alt="摄像头画面">
    </div>

    <script>
        let screenElement = document.getElementById('screen');
        let fullscreenScreen = document.getElementById('fullscreen-screen');
        let fullscreenContainer = document.getElementById('fullscreen-container');
        let cmdContainer = document.getElementById('cmd-container');
        let cameraContainer = document.getElementById('camera-container');
        let statusElement = document.getElementById('status');
        let lastMouseX = 0;
        let lastMouseY = 0;
        let isFullscreen = false;
        let lastActionTime = 0;
        const ACTION_COOLDOWN = 100; // 100ms动作冷却时间
        
        // 图像加载错误处理
        function handleImageError(img) {
            console.log('图像加载失败，尝试重新加载');
            setTimeout(() => {
                img.src = '/video_feed?_=' + new Date().getTime();
            }, 500);
        }
        
        // 鼠标事件处理函数
        function handleMouseEvent(element, event) {
            if (Date.now() - lastActionTime < ACTION_COOLDOWN) return;
            lastActionTime = Date.now();
            
            const rect = element.getBoundingClientRect();
            const x = event.clientX - rect.left;
            const y = event.clientY - rect.top;
            
            const isFullscreenElement = element === fullscreenScreen;
            const screenWidth = isFullscreenElement ? rect.width : screenElement.getBoundingClientRect().width;
            const screenHeight = isFullscreenElement ? rect.height : screenElement.getBoundingClientRect().height;
            
            return { x, y, screenWidth, screenHeight, isFullscreenElement };
        }

        // 鼠标移动事件
        function setupMouseMove(element) {
            element.addEventListener('mousemove', function(e) {
                const eventData = handleMouseEvent(element, e);
                if (!eventData) return;
                
                fetch('/mouse_move', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        x: eventData.x,
                        y: eventData.y,
                        screen_width: eventData.screenWidth,
                        screen_height: eventData.screenHeight,
                        fullscreen: eventData.isFullscreenElement
                    })
                });
            });
        }
        
        // 鼠标点击事件
        function setupMouseClick(element) {
            element.addEventListener('click', function(e) {
                if (e.target === document.querySelector('.exit-fullscreen') || 
                    e.target === document.querySelector('.cmd-close') || 
                    e.target === document.querySelector('.camera-close')) return;
                
                const eventData = handleMouseEvent(element, e);
                if (!eventData) return;
                
                fetch('/mouse_click', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        x: eventData.x,
                        y: eventData.y,
                        screen_width: eventData.screenWidth,
                        screen_height: eventData.screenHeight,
                        fullscreen: eventData.isFullscreenElement
                    })
                });
            });
        }
        
        // 右键点击事件
        function setupContextMenu(element) {
            element.addEventListener('contextmenu', function(e) {
                e.preventDefault();
                const eventData = handleMouseEvent(element, e);
                if (!eventData) return;
                
                fetch('/mouse_right_click', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        x: eventData.x,
                        y: eventData.y,
                        screen_width: eventData.screenWidth,
                        screen_height: eventData.screenHeight,
                        fullscreen: eventData.isFullscreenElement
                    })
                });
            });
        }
        
        // 滚轮事件
        function setupWheel(element) {
            element.addEventListener('wheel', function(e) {
                if (Date.now() - lastActionTime < ACTION_COOLDOWN) return;
                lastActionTime = Date.now();
                
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
        }
        
        // 全屏控制
        function toggleFullscreen() {
            isFullscreen = !isFullscreen;
            fullscreenContainer.style.display = isFullscreen ? 'flex' : 'none';
            statusElement.textContent = isFullscreen ? '📺 全屏模式' : '🖥️ 窗口模式';
            
            if (isFullscreen) {
                // 全屏时重新加载图像
                fullscreenScreen.src = '/video_feed?_=' + new Date().getTime();
            }
        }
        
        function exitFullscreen() {
            isFullscreen = false;
            fullscreenContainer.style.display = 'none';
            statusElement.textContent = '🖥️ 窗口模式';
        }
        
        // CMD面板控制
        function toggleCmdPanel() {
            const container = document.getElementById('cmd-container');
            const isVisible = container.style.display === 'block';
            container.style.display = isVisible ? 'none' : 'block';
            if (!isVisible) {
                document.getElementById('cmd-output').innerHTML = '';
            }
        }
        
        function closeCmdPanel() {
            document.getElementById('cmd-container').style.display = 'none';
        }
        
        function handleCmdKeyPress(event) {
            if (event.key === 'Enter') {
                executeCmd();
            }
        }
        
        function executeCmd() {
            const cmdInput = document.getElementById('cmd-input');
            const cmdOutput = document.getElementById('cmd-output');
            const command = cmdInput.value.trim();
            
            if (!command) return;
            
            // 显示命令
            cmdOutput.innerHTML += `<div>&gt; ${command}</div>`;
            
            fetch('/execute_cmd', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ command: command })
            })
            .then(response => response.json())
            .then(data => {
                cmdOutput.innerHTML += `<div>${data.output.replace(/\\n/g, '<br>')}</div>`;
                cmdOutput.scrollTop = cmdOutput.scrollHeight;
            })
            .catch(error => {
                cmdOutput.innerHTML += `<div style="color: red;">执行错误: ${error.message}</div>`;
                cmdOutput.scrollTop = cmdOutput.scrollHeight;
            });
            
            cmdInput.value = '';
        }
        
        // 摄像头面板控制
        function toggleCameraPanel() {
            const container = document.getElementById('camera-container');
            const feed = document.getElementById('camera-feed');
            const isVisible = container.style.display === 'block';
            
            if (isVisible) {
                container.style.display = 'none';
                feed.src = ''; // 停止摄像头流
            } else {
                container.style.display = 'block';
                feed.src = '/camera_feed'; // 开始摄像头流
            }
        }
        
        function closeCameraPanel() {
            const container = document.getElementById('camera-container');
            const feed = document.getElementById('camera-feed');
            container.style.display = 'none';
            feed.src = '';
        }
        
        // 麦克风控制
        function toggleMicrophone() {
            fetch('/toggle_microphone', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ enabled: !Config.microphone_enabled })
            })
            .then(response => response.json())
            .then(data => {
                statusElement.textContent = data.enabled ? '🎤 麦克风已开启' : '🎤 麦克风已关闭';
            })
            .catch(error => {
                statusElement.textContent = `❌ 麦克风控制失败: ${error.message}`;
            });
        }
        
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
        
        // 设置所有事件监听器
        setupMouseMove(screenElement);
        setupMouseMove(fullscreenScreen);
        setupMouseClick(screenElement);
        setupMouseClick(fullscreenScreen);
        setupContextMenu(screenElement);
        setupContextMenu(fullscreenScreen);
        setupWheel(screenElement);
        setupWheel(fullscreenScreen);
        
        // 定期刷新屏幕
        setInterval(function() {
            if (!isFullscreen) {
                screenElement.src = '/video_feed?_=' + new Date().getTime();
            }
        }, 100);
        
        // 全屏屏幕刷新
        setInterval(function() {
            if (isFullscreen) {
                fullscreenScreen.src = '/video_feed?_=' + new Date().getTime();
            }
        }, 50);
        
        // 摄像头刷新
        setInterval(function() {
            if (document.getElementById('camera-container').style.display === 'block') {
                document.getElementById('camera-feed').src = '/camera_feed?_=' + new Date().getTime();
            }
        }, 100);
        
        // 键盘快捷键
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && isFullscreen) {
                exitFullscreen();
            }
            if (e.key === 'F11') {
                e.preventDefault();
                toggleFullscreen();
            }
        });
        
        // 页面加载完成后的处理
        window.onload = function() {
            statusElement.textContent = '✅ 连接成功';
        };
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    local_ip = get_local_ip()
    resolution_str = f"{Config.resolution[0]}x{Config.resolution[1]}"
    return render_template_string(HTML_TEMPLATE, 
                                ip=local_ip, 
                                port=5000,
                                resolution=resolution_str)

@app.route('/video_feed')
def video_feed():
    global screen_thread
    if screen_thread:
        frame = screen_thread.get_frame()
        if frame:
            return Response(frame, mimetype='image/jpeg')
    return '', 404

@app.route('/camera_feed')
def camera_feed():
    global camera_thread
    if camera_thread and Config.camera_enabled:
        frame = camera_thread.get_frame()
        if frame:
            return Response(frame, mimetype='image/jpeg')
    return '', 404

@app.route('/execute_cmd', methods=['POST'])
def execute_cmd():
    try:
        data = request.json
        if not data or 'command' not in data:
            return jsonify({'status': 'error', 'output': 'Invalid request'}), 400
            
        command = data['command']
        
        try:
            # 执行命令
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30  # 30秒超时
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
            # 启动麦克风
            with screen_thread_lock:
                if microphone_thread is None:
                    microphone_thread = MicrophoneCaptureThread()
                    microphone_thread.start()
            Config.microphone_enabled = True
        elif not enabled and Config.microphone_enabled:
            # 停止麦克风
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
            # 启动摄像头
            with screen_thread_lock:
                if camera_thread is None:
                    camera_thread = CameraCaptureThread()
                    camera_thread.start()
            Config.camera_enabled = True
        elif not enabled and Config.camera_enabled:
            # 停止摄像头
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
        
        # 获取实际屏幕尺寸
        screen_size = pyautogui.size()
        
        # 计算实际坐标
        if fullscreen:
            # 全屏模式下直接使用比例
            actual_x = x * (screen_size.width / screen_width)
            actual_y = y * (screen_size.height / screen_height)
        else:
            # 窗口模式下需要考虑比例
            aspect_ratio = screen_width / screen_height
            screen_aspect = screen_size.width / screen_size.height
            
            if aspect_ratio > screen_aspect:
                # 宽度受限，高度居中
                new_height = screen_size.height
                new_width = screen_size.height * aspect_ratio
                offset_x = (screen_size.width - new_width) / 2
                offset_y = 0
                actual_x = offset_x + (x * (new_width / screen_width))
                actual_y = y * (new_height / screen_height)
            else:
                # 高度受限，宽度居中
                new_width = screen_size.width
                new_height = screen_size.width / aspect_ratio
                offset_x = 0
                offset_y = (screen_size.height - new_height) / 2
                actual_x = x * (new_width / screen_width)
                actual_y = offset_y + (y * (new_height / screen_height))
        
        # 确保坐标在屏幕范围内
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
        
        # 获取实际屏幕尺寸
        screen_size = pyautogui.size()
        
        # 计算实际坐标（使用与mouse_move相同的逻辑）
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
        pyautogui.scroll(int(-delta/50))  # 调整滚轮灵敏度
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
        
        # 处理特殊键
        special_keys = ['ctrl', 'alt', 'shift', 'win', 'cmd', 'esc', 'enter', 'space', 'tab',
                       'backspace', 'delete', 'home', 'end', 'pageup', 'pagedown',
                       'up', 'down', 'left', 'right']
        
        if key.lower() in special_keys:
            pyautogui.press(key.lower())
        elif len(key) == 1:
            pyautogui.write(key)
        else:
            # 组合键处理，例如 "ctrl+c"
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
            # 启动屏幕捕获线程
            with screen_thread_lock:
                screen_thread = ScreenCaptureThread()
                screen_thread.start()
            
            # 启动摄像头线程（默认开启）
            if Config.camera_enabled:
                with screen_thread_lock:
                    camera_thread = CameraCaptureThread()
                    camera_thread.start()
            
            # 启动麦克风线程
            if Config.microphone_enabled:
                with screen_thread_lock:
                    microphone_thread = MicrophoneCaptureThread()
                    microphone_thread.start()
            
            local_ip = get_local_ip()
            self.server_started.emit(local_ip, self.port)
            
            self.running = True
            with server_running_lock:
                server_running = True
            
            # 启动Flask服务器
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
        
        # 创建托盘菜单
        menu = QMenu(parent)
        
        # 显示主窗口
        show_action = QAction("显示主窗口", parent)
        show_action.triggered.connect(self.show_main_window)
        menu.addAction(show_action)
        
        # 最小化到托盘
        minimize_action = QAction("最小化到托盘", parent)
        minimize_action.triggered.connect(self.minimize_to_tray)
        menu.addAction(minimize_action)
        
        # 启动服务
        start_action = QAction("启动服务", parent)
        start_action.triggered.connect(self.start_service)
        menu.addAction(start_action)
        
        # 停止服务
        stop_action = QAction("停止服务", parent)
        stop_action.triggered.connect(self.stop_service)
        menu.addAction(stop_action)
        
        # 分隔符
        menu.addSeparator()
        
        # 退出程序
        exit_action = QAction("退出程序", parent)
        exit_action.triggered.connect(self.exit_program)
        menu.addAction(exit_action)
        
        self.setContextMenu(menu)
        self.activated.connect(self.on_tray_icon_activated)
        
        # 保存主窗口引用
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

# PyQt5主窗口 - 深色主题
class RemoteControlGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("远程控制被控端")
        self.setMinimumSize(800, 700)
        
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
        title_label.setStyleSheet("color: #667eea; margin: 10px 0; text-shadow: 0 2px 4px rgba(0,0,0,0.5);")
        main_layout.addWidget(title_label)
        
        # 服务器配置组
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
        
        # 视频设置组
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
        
        # 分辨率设置
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
        self.resolution_combo.setCurrentIndex(3)  # 默认1280x720
        self.resolution_combo.setStyleSheet("""
            QComboBox {
                padding: 5px;
                border: 2px solid #444;
                border-radius: 5px;
                background: #333;
                color: white;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox::down-arrow {
                image: none;
            }
        """)
        video_layout.addRow("分辨率:", self.resolution_combo)
        
        # 帧率设置
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(['15 FPS', '30 FPS', '45 FPS', '60 FPS'])
        self.fps_combo.setCurrentIndex(1)  # 默认30 FPS
        self.fps_combo.setStyleSheet("""
            QComboBox {
                padding: 5px;
                border: 2px solid #444;
                border-radius: 5px;
                background: #333;
                color: white;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox::down-arrow {
                image: none;
            }
        """)
        video_layout.addRow("帧率:", self.fps_combo)
        
        # 画质设置
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
        
        # 功能开关组
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
            QCheckBox::indicator {
                width: 20px;
                height: 20px;
            }
            QCheckBox::indicator:checked {
                background: #4ecdc4;
            }
        """)
        self.camera_checkbox.setChecked(True)  # 默认勾选
        features_layout.addWidget(self.camera_checkbox)
        
        self.mic_checkbox = QCheckBox("启用麦克风")
        self.mic_checkbox.setStyleSheet("""
            QCheckBox {
                color: #e0e0e0;
                font-size: 14px;
            }
            QCheckBox::indicator {
                width: 20px;
                height: 20px;
            }
            QCheckBox::indicator:checked {
                background: #4ecdc4;
            }
        """)
        features_layout.addWidget(self.mic_checkbox)
        
        features_group.setLayout(features_layout)
        main_layout.addWidget(features_group)
        
        # 状态显示
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
                box-shadow: 0 4px 8px rgba(0,0,0,0.3);
            }
            QPushButton:hover {
                background: linear-gradient(45deg, #44a08d, #3a8c78);
                transform: translateY(-2px);
                box-shadow: 0 6px 12px rgba(0,0,0,0.4);
            }
            QPushButton:disabled {
                background: #555;
                color: #888;
                transform: none;
                box-shadow: none;
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
                box-shadow: 0 4px 8px rgba(0,0,0,0.3);
            }
            QPushButton:hover {
                background: linear-gradient(45deg, #ee5a5a, #d44a4a);
                transform: translateY(-2px);
                box-shadow: 0 6px 12px rgba(0,0,0,0.4);
            }
            QPushButton:disabled {
                background: #555;
                color: #888;
                transform: none;
                box-shadow: none;
            }
        """)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_server)
        button_layout.addWidget(self.stop_btn)
        
        # 最小化到托盘按钮
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
                box-shadow: 0 4px 8px rgba(0,0,0,0.3);
            }
            QPushButton:hover {
                background: linear-gradient(45deg, #9bbefc, #b8d8f8);
                transform: translateY(-2px);
                box-shadow: 0 6px 12px rgba(0,0,0,0.4);
            }
        """)
        self.minimize_btn.clicked.connect(self.minimize_to_tray)
        button_layout.addWidget(self.minimize_btn)
        
        main_layout.addLayout(button_layout)
        
        # 状态栏
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
        
        # 初始化Flask线程
        self.flask_thread = None
        
        # 检查端口有效性
        self.port_input.textChanged.connect(self.validate_port)
        
        # 设置窗口背景
        self.setStyleSheet("""
            QMainWindow {
                background: linear-gradient(135deg, #0d0d0d 0%, #1a1a1a 100%);
            }
            QWidget {
                background: transparent;
            }
        """)
        
        # 初始化系统托盘图标
        try:
            self.tray_icon = SystemTrayIcon("app.ico", self)
            Config.tray_icon = self.tray_icon
        except Exception as e:
            print(f"托盘图标初始化失败: {e}")
            self.tray_icon = None
        
        # 隐藏窗口到托盘
        QTimer.singleShot(100, self.hide_to_tray)
        
        # 5秒后自动启动服务器
        QTimer.singleShot(5000, self.auto_start_server)
    
    def auto_start_server(self):
        """5秒后自动启动服务器"""
        print("5秒延迟结束，自动启动服务器...")
        self.start_server()
    
    def minimize_to_tray(self):
        """最小化到托盘"""
        self.hide()
        if self.tray_icon:
            self.tray_icon.showMessage("远程控制", "程序已最小化到托盘", QSystemTrayIcon.Information, 2000)
    
    def hide_to_tray(self):
        """启动时自动隐藏到托盘"""
        self.hide()
        if self.tray_icon:
            self.tray_icon.showMessage("远程控制", "程序已启动并隐藏到托盘", QSystemTrayIcon.Information, 2000)
    
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
    
    def get_video_settings(self):
        """解析视频设置"""
        # 解析分辨率
        resolution_text = self.resolution_combo.currentText()
        resolution_str = resolution_text.split()[0]  # 获取 "1280x720" 部分
        width, height = map(int, resolution_str.split('x'))
        
        # 解析帧率
        fps_text = self.fps_combo.currentText()
        fps = int(fps_text.split()[0])
        
        # 获取画质
        quality = self.quality_slider.value()
        
        return (width, height), fps, quality
    
    def start_server(self):
        port_text = self.port_input.text()
        if not self.validate_port(port_text):
            QMessageBox.warning(self, "错误", "请输入有效的端口号 (1024-65535)")
            return
        
        port = int(port_text)
        
        # 获取视频设置
        resolution, fps, quality = self.get_video_settings()
        
        # 获取功能开关设置
        camera_enabled = self.camera_checkbox.isChecked()
        mic_enabled = self.mic_checkbox.isChecked()
        
        # 更新全局配置
        Config.resolution = resolution
        Config.fps = fps
        Config.jpeg_quality = quality
        Config.camera_enabled = camera_enabled
        Config.microphone_enabled = mic_enabled
        
        # 禁用开始按钮，启用停止按钮
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.port_input.setEnabled(False)
        # 禁用视频设置
        self.resolution_combo.setEnabled(False)
        self.fps_combo.setEnabled(False)
        self.quality_slider.setEnabled(False)
        # 禁用功能开关
        self.camera_checkbox.setEnabled(False)
        self.mic_checkbox.setEnabled(False)
        
        # 更新状态
        self.status_label.setText("🟡 正在启动服务器...")
        self.status_label.setStyleSheet("color: #ffd166; padding: 10px;")
        self.statusBar.showMessage("正在启动服务器...")
        
        # 创建并启动Flask线程
        self.flask_thread = FlaskThread(port)
        self.flask_thread.server_started.connect(self.on_server_started)
        self.flask_thread.server_stopped.connect(self.on_server_stopped)
        self.flask_thread.start()
    
    def stop_server(self):
        if self.flask_thread and self.flask_thread.isRunning():
            self.status_label.setText("🟡 正在停止服务器...")
            self.status_label.setStyleSheet("color: #ffd166; padding: 10px;")
            self.statusBar.showMessage("正在停止服务器...")
            
            # 停止服务器
            self.flask_thread.stop()
    
    def start_server_from_tray(self):
        """从托盘启动服务"""
        if not server_running:
            self.start_server()
    
    def stop_server_from_tray(self):
        """从托盘停止服务"""
        if server_running:
            self.stop_server()
    
    def on_server_started(self, ip, port):
        # 更新UI
        self.status_label.setText("🟢 服务器运行中")
        self.status_label.setStyleSheet("color: #4ecdc4; padding: 10px;")
        self.ip_label.setText(f"IP地址: {ip}")
        self.url_label.setText(f"访问地址: http://{ip}:{port}")
        resolution_str = f"{Config.resolution[0]}x{Config.resolution[1]}"
        status_msg = f"服务器已启动，访问地址: http://{ip}:{port}，分辨率: {resolution_str}"
        if Config.camera_enabled:
            status_msg += "，摄像头: 已启用"
        if Config.microphone_enabled:
            status_msg += "，麦克风: 已启用"
        self.statusBar.showMessage(status_msg)
        
        # 启用停止按钮
        self.stop_btn.setEnabled(True)
    
    def on_server_stopped(self):
        # 更新UI
        self.status_label.setText("🔴 服务器已停止")
        self.status_label.setStyleSheet("color: #ff6b6b; padding: 10px;")
        self.ip_label.setText("IP地址: -")
        self.url_label.setText("访问地址: -")
        self.statusBar.showMessage("服务器已停止")
        
        # 重置按钮状态
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.port_input.setEnabled(True)
        # 启用视频设置
        self.resolution_combo.setEnabled(True)
        self.fps_combo.setEnabled(True)
        self.quality_slider.setEnabled(True)
        # 启用功能开关
        self.camera_checkbox.setEnabled(True)
        self.mic_checkbox.setEnabled(True)
        
        # 清理线程
        self.flask_thread = None
    
    def close_from_tray(self):
        """从托盘退出程序"""
        self.close()
    
    def closeEvent(self, event):
        # 确保在关闭窗口时停止服务器
        if self.flask_thread and self.flask_thread.isRunning():
            reply = QMessageBox.question(self, '确认退出',
                                        '服务器正在运行，确定要退出吗？',
                                        QMessageBox.Yes | QMessageBox.No,
                                        QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.stop_server()
                # 等待线程真正结束
                if self.flask_thread.isRunning():
                    self.flask_thread.wait(2000)  # 最多等待2秒
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

def run_gui():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')  # 使用现代风格
    
    # 设置深色调色板
    from PyQt5.QtGui import QColor
    palette = app.palette()
    palette.setColor(palette.Window, QColor(30, 30, 30))
    palette.setColor(palette.WindowText, QColor(224, 224, 224))
    palette.setColor(palette.Base, QColor(45, 45, 45))
    palette.setColor(palette.AlternateBase, QColor(53, 53, 53))
    palette.setColor(palette.ToolTipBase, QColor(224, 224, 224))
    palette.setColor(palette.ToolTipText, QColor(224, 224, 224))
    palette.setColor(palette.Text, QColor(224, 224, 224))
    palette.setColor(palette.Button, QColor(60, 60, 60))
    palette.setColor(palette.ButtonText, QColor(224, 224, 224))
    palette.setColor(palette.BrightText, QColor(255, 0, 0))
    palette.setColor(palette.Highlight, QColor(102, 126, 234))
    palette.setColor(palette.HighlightedText, QColor(0, 0, 0))
    app.setPalette(palette)
    
    window = RemoteControlGUI()
    
    sys.exit(app.exec_())


if __name__ == '__main__':
    run_gui()