
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
from flask import Flask, render_template_string, request, jsonify, Response
from PIL import ImageGrab
import pyautogui
import cv2
import numpy as np

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
    
    def get_frame(self):
        with self.lock:
            return self.last_frame if self.last_frame else b''

# 全局变量
screen_thread = ScreenCaptureThread()
screen_thread.start()

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
    frame = screen_thread.get_frame()
    if frame:
        return Response(frame, mimetype='image/jpeg')
    else:
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

def run_server():
    local_ip = get_local_ip()
    print(f"🚀 远程控制服务器已启动!")
    print(f"🌐 访问地址: http://{local_ip}:5000")
    print(f"🏠 本地访问: http://127.0.0.1:5000")
    print("💡 提示: 在浏览器中打开上述地址即可进行远程控制")
    print("🛑 按 Ctrl+C 停止服务器")
    
    app.run(host='0.0.0.0', port=5000, threaded=True, debug=False)


if __name__ == '__main__':
    run_server()