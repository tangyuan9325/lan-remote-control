# -*- coding: utf-8 -*-
"""Flask app factory + endpoints for the RemoteControl 1.4 refactor."""

import os
import io
import time
import wave
import shlex
import socket
import subprocess
import threading

from flask import Flask, request, jsonify, Response, send_file, render_template_string

try:
    import pyautogui
    pyautogui.FAILSAFE = False
except Exception:  # headless dev box without display
    pyautogui = None

from .core import (
    Config, VERSION, check_password, get_local_ip, mjpeg_stream,
    safe_upload_path, ensure_screen_worker, ensure_camera_worker,
    ensure_mic_worker, screen_thread,
    acquire_stream, release_stream, active_streams, MAX_CONCURRENT_STREAMS,
)
from .webui import HTML_TEMPLATE

MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # 512 MB cap


def _managed_stream(gen, on_finish):
    """Wrap a generator so `on_finish` runs when the stream ends by ANY route:
    normal completion, client disconnect (GeneratorExit) or internal error."""
    try:
        for chunk in gen:
            yield chunk
    finally:
        try:
            gen.close()
        except Exception:
            pass
        on_finish()


def _auth_ok():
    cand = request.args.get("password", "") or (request.json or {}).get("password", "") \
        if request.is_json else request.args.get("password", "")
    if not cand:
        # cookie fallback: the web UI stores the token after the password gate,
        # so every XHR/fetch and <img src> request authenticates automatically.
        import urllib.parse
        cand = urllib.parse.unquote(request.cookies.get("rc_token", ""))
    return check_password(cand)


def _map_to_screen(data):
    """Translate web coordinates to real screen pixels (letterbox aware).

    Fixes the legacy fullscreen branch which multiplied raw px by a ratio of
    ratios (>1 factor => cursor always landed off-target in fullscreen).
    """
    if pyautogui is None:
        return None
    x = float(data.get("x", 0))
    y = float(data.get("y", 0))
    sw = float(data.get("screen_width") or 1)
    sh = float(data.get("screen_height") or 1)
    size = pyautogui.size()
    # normalized position inside the displayed image first
    nx = max(0.0, min(1.0, x / sw))
    ny = max(0.0, min(1.0, y / sh))
    # object-fit: contain letterboxing math
    img_aspect = Config.resolution[0] / float(Config.resolution[1])
    box_aspect = sw / sh
    if box_aspect > img_aspect:
        vis_w = sh * img_aspect
        off_x = (sw - vis_w) / 2.0
        rel_x = max(0.0, min(1.0, (x - off_x) / vis_w)) if vis_w > 0 else nx
        rel_y = ny
    else:
        vis_h = sw / img_aspect
        off_y = (sh - vis_h) / 2.0
        rel_x = nx
        rel_y = max(0.0, min(1.0, (y - off_y) / vis_h)) if vis_h > 0 else ny
    ax = int(rel_x * (size.width - 1))
    ay = int(rel_y * (size.height - 1))
    return max(0, min(ax, size.width - 1)), max(0, min(ay, size.height - 1))


def create_app(port=5000):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    @app.after_request
    def _sec_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp

    @app.route("/")
    def index():
        resolution_str = "%dx%d" % Config.resolution
        return render_template_string(
            HTML_TEMPLATE,
            ip=get_local_ip(), port=port, version=VERSION,
            resolution=resolution_str, fps=Config.fps, quality=Config.jpeg_quality,
            pwd_state="已启用" if Config.password else "未设置",
            cmd_enabled_js="true" if Config.enable_cmd else "false",
        )

    @app.route("/api_status")
    def api_status():
        return jsonify({
            "ok": True,
            "requires_password": bool(Config.password),
            "cmd_enabled": bool(Config.enable_cmd),
            "resolution": "%dx%d" % Config.resolution,
            "fps": Config.fps,
            "quality": Config.jpeg_quality,
        })

    # ---------------- video / camera / audio feeds ----------------
    @app.route("/video_feed")
    def video_feed():
        if not _auth_ok():
            return jsonify({"error": "unauthorized"}), 401
        if not acquire_stream():
            return jsonify({"error": "too many viewers"}), 503
        worker = ensure_screen_worker()
        interval = 1.0 / max(1, worker.fps)
        stop_flag = {"v": True}

        def getter():
            if not server_state.get("running", True):
                stop_flag["v"] = False
                return b""
            worker.touch()
            # skip duplicates: only hand out a frame when the encoder produced
            # a NEW one (static screen => zero bytes on the wire, zero encode)
            gen = worker.last_generation()
            if getter.last_gen == gen and not getter.first:
                return b""
            getter.first = False
            getter.last_gen = gen
            return worker.get_frame()
        getter.last_gen = -1
        getter.first = True

        def cleanup():
            release_stream()

        stream = _managed_stream(mjpeg_stream(getter, interval,
                                              stop_check=lambda: stop_flag["v"]),
                                 cleanup)
        return Response(stream, mimetype="multipart/x-mixed-replace; boundary=frameboundary",
                        headers={"Cache-Control": "no-store"})

    @app.route("/camera_feed")
    def camera_feed():
        if not _auth_ok():
            return jsonify({"error": "unauthorized"}), 401
        if not Config.camera_enabled:
            return jsonify({"error": "camera disabled"}), 403
        if not acquire_stream():
            return jsonify({"error": "too many viewers"}), 503
        worker = ensure_camera_worker()

        def getter():
            worker.touch()
            return worker.get_frame()

        stream = _managed_stream(
            mjpeg_stream(getter, 1.0 / 15, lambda: server_state.get("running", True)),
            release_stream)
        return Response(stream, mimetype="multipart/x-mixed-replace; boundary=frameboundary",
                        headers={"Cache-Control": "no-store"})

    @app.route("/audio_feed")
    def audio_feed():
        if not _auth_ok():
            return jsonify({"error": "unauthorized"}), 401
        if not Config.microphone_enabled:
            return jsonify({"error": "microphone disabled"}), 403
        worker = ensure_mic_worker()
        worker.touch()
        data = worker.get_audio()
        if not data:
            return "", 204
        # wrap PCM into a tiny wav so the browser can decode it easily
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(worker.channels)
            wf.setsampwidth(2)
            wf.setframerate(worker.rate)
            wf.writeframes(data)
        return Response(buf.getvalue(), mimetype="audio/wav",
                        headers={"Cache-Control": "no-store"})

    # ---------------- input ----------------
    @app.route("/mouse_move", methods=["POST"])
    def mouse_move():
        if not _auth_ok():
            return jsonify({"status": "error", "message": "unauthorized"}), 401
        data = request.get_json(silent=True)
        if not data or "x" not in data or "y" not in data:
            return jsonify({"status": "error", "message": "Invalid request"}), 400
        pos = _map_to_screen(data)
        if pos is None:
            return jsonify({"status": "error", "message": "input unavailable"}), 503
        try:
            pyautogui.moveTo(pos[0], pos[1])
            return jsonify({"status": "success"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    def _click_route(fn_name):
        fn = getattr(pyautogui, fn_name)
        data = request.get_json(silent=True)
        if not data or "x" not in data or "y" not in data:
            return jsonify({"status": "error", "message": "Invalid request"}), 400
        pos = _map_to_screen(data)
        if pos is None:
            return jsonify({"status": "error", "message": "input unavailable"}), 503
        fn(pos[0], pos[1])
        return jsonify({"status": "success"})

    @app.route("/mouse_click", methods=["POST"])
    def mouse_click():
        if not _auth_ok():
            return jsonify({"status": "error", "message": "unauthorized"}), 401
        if pyautogui is None:
            return jsonify({"status": "error", "message": "input unavailable"}), 503
        try:
            return _click_route("click")
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/mouse_right_click", methods=["POST"])
    def mouse_right_click():
        if not _auth_ok():
            return jsonify({"status": "error", "message": "unauthorized"}), 401
        if pyautogui is None:
            return jsonify({"status": "error", "message": "input unavailable"}), 503
        try:
            return _click_route("rightClick")
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/mouse_scroll", methods=["POST"])
    def mouse_scroll():
        if not _auth_ok():
            return jsonify({"status": "error", "message": "unauthorized"}), 401
        data = request.get_json(silent=True)
        if not data or "delta" not in data:
            return jsonify({"status": "error", "message": "Invalid request"}), 400
        if pyautogui is None:
            return jsonify({"status": "error", "message": "input unavailable"}), 503
        try:
            steps = max(-20, min(20, int(-float(data["delta"]) / 50)))
            pyautogui.scroll(steps)
            return jsonify({"status": "success"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/keyboard", methods=["POST"])
    def keyboard():
        if not _auth_ok():
            return jsonify({"status": "error", "message": "unauthorized"}), 401
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"status": "error", "message": "Invalid request"}), 400
        if pyautogui is None:
            return jsonify({"status": "error", "message": "input unavailable"}), 503
        try:
            text = data.get("text")
            if isinstance(text, str) and text:
                pyautogui.typewrite(text[:512]) if text.isascii() and all(
                    32 <= ord(c) < 127 for c in text) else pyautogui.write(
                    "".join(c for c in text if c.isprintable())[:512])
                return jsonify({"status": "success"})
            key = data.get("key")
            if not isinstance(key, str) or not key or len(key) > 64:
                return jsonify({"status": "error", "message": "Invalid request"}), 400
            special = {"esc", "enter", "space", "tab", "backspace", "delete", "home",
                       "end", "pageup", "pagedown", "up", "down", "left", "right",
                       "ctrl", "alt", "shift", "win", "cmd", "f1", "f2", "f3", "f4",
                       "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12"}
            k = key.lower()
            if "+" in k:
                keys = [p.strip() for p in k.split("+") if p.strip()]
                if 1 < len(keys) <= 4 and all(x in special or (len(x) == 1 and x.isalnum()) for x in keys):
                    pyautogui.hotkey(*keys)
                    return jsonify({"status": "success"})
                return jsonify({"status": "error", "message": "bad combo"}), 400
            if k in special:
                pyautogui.press(k)
                return jsonify({"status": "success"})
            if len(k) == 1:
                pyautogui.press(k)
                return jsonify({"status": "success"})
            return jsonify({"status": "error", "message": "unknown key"}), 400
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/set_quality", methods=["POST"])
    def set_quality():
        if not _auth_ok():
            return jsonify({"status": "error", "message": "unauthorized"}), 401
        data = request.get_json(silent=True)
        if not data or "quality" not in data:
            return jsonify({"status": "error", "message": "Invalid request"}), 400
        try:
            q = int(data["quality"])
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "quality must be int"}), 400
        q = max(10, min(95, q))
        Config.jpeg_quality = q
        w = ensure_screen_worker()
        w.update_settings(quality=q)
        return jsonify({"status": "success", "quality": q})

    # ---------------- toggles ----------------
    @app.route("/toggle_microphone", methods=["POST"])
    def toggle_microphone():
        if not _auth_ok():
            return jsonify({"status": "error", "message": "unauthorized"}), 401
        data = request.get_json(silent=True)
        if not data or "enabled" not in data:
            return jsonify({"status": "error", "message": "Invalid request"}), 400
        Config.microphone_enabled = bool(data["enabled"])
        if Config.microphone_enabled:
            ensure_mic_worker()
        return jsonify({"enabled": Config.microphone_enabled})

    @app.route("/toggle_camera", methods=["POST"])
    def toggle_camera():
        if not _auth_ok():
            return jsonify({"status": "error", "message": "unauthorized"}), 401
        data = request.get_json(silent=True)
        if not data or "enabled" not in data:
            return jsonify({"status": "error", "message": "Invalid request"}), 400
        Config.camera_enabled = bool(data["enabled"])
        if Config.camera_enabled:
            ensure_camera_worker()
        return jsonify({"enabled": Config.camera_enabled})

    # ---------------- files ----------------
    @app.route("/upload_file", methods=["POST"])
    def upload_file():
        if not _auth_ok():
            return jsonify({"success": False, "error": "unauthorized"}), 401
        try:
            f = request.files.get("file")
            if f is None or f.filename == "":
                return jsonify({"success": False, "error": "没有文件被上传"})
            target = safe_upload_path(os.path.basename(f.filename))
            if not target:
                return jsonify({"success": False, "error": "非法文件名"})
            # enforce quota while streaming to disk (no full-file RAM buffering)
            remaining = MAX_UPLOAD_BYTES
            with open(target, "wb") as out:
                while True:
                    chunk = f.stream.read(64 * 1024)
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    if remaining < 0:
                        out.close()
                        try:
                            os.remove(target)
                        except OSError:
                            pass
                        return jsonify({"success": False, "error": "文件超过 512MB 限制"}), 413
                    out.write(chunk)
            return jsonify({"success": True, "message": "文件上传成功"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/list_files")
    def list_files():
        if not _auth_ok():
            return jsonify({"success": False, "error": "unauthorized"}), 401
        try:
            files = []
            base = Config.upload_dir
            for name in os.listdir(base):
                p = os.path.join(base, name)
                if os.path.isfile(p) and not os.path.islink(p):
                    st = os.stat(p)
                    files.append({"name": name, "size": st.st_size, "modified": st.st_mtime})
            files.sort(key=lambda x: x["modified"], reverse=True)
            return jsonify({"success": True, "files": files[:500]})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/download_file/<path:filename>")
    def download_file(filename):
        if not _auth_ok():
            return jsonify({"error": "unauthorized"}), 401
        target = safe_upload_path(filename)
        if not target or not os.path.isfile(target):
            return "文件不存在", 404
        try:
            return send_file(target, as_attachment=True, download_name=os.path.basename(target))
        except Exception as e:
            return "下载失败: %s" % e, 500

    @app.route("/delete_file/<path:filename>", methods=["DELETE"])
    def delete_file(filename):
        if not _auth_ok():
            return jsonify({"success": False, "error": "unauthorized"}), 401
        target = safe_upload_path(filename)
        if not target or not os.path.isfile(target):
            return jsonify({"success": False, "error": "文件不存在"}), 404
        try:
            os.remove(target)
            return jsonify({"success": True, "message": "文件删除成功"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    # ---------------- command execution (opt-in) ----------------
    @app.route("/execute_cmd", methods=["POST"])
    def execute_cmd():
        if not _auth_ok():
            return jsonify({"status": "error", "output": "unauthorized"}), 401
        if not Config.enable_cmd:
            return jsonify({"status": "error",
                            "output": "命令执行已被禁用（安全默认）。请在启动时添加 --enable-cmd。"}), 403
        data = request.get_json(silent=True)
        if not data or "command" not in data:
            return jsonify({"status": "error", "output": "Invalid request"}), 400
        command = str(data["command"])[:4000]
        try:
            use_shell = os.name == "nt"  # cmd.exe handles builtins like dir/cd
            args = command if use_shell else shlex.split(command)
            result = subprocess.run(
                args, shell=use_shell, capture_output=True, text=True, timeout=30,
            )
            output = (result.stdout or "") + (result.stderr or "")
            if not output.strip():
                output = "(无输出)"
            return jsonify({"status": "success", "output": output[:200000]})
        except subprocess.TimeoutExpired:
            return jsonify({"status": "error", "output": "命令执行超时(30s)"})
        except Exception as e:
            return jsonify({"status": "error", "output": "命令执行失败: %s" % e}), 500

    # ---------------- system power ----------------
    @app.route("/system", methods=["POST"])
    def system():
        if not _auth_ok():
            return jsonify({"status": "error", "message": "unauthorized"}), 401
        data = request.get_json(silent=True)
        if not data or "action" not in data:
            return jsonify({"status": "error", "message": "Invalid request"}), 400
        action = str(data["action"]).lower()
        plan = {
            "shutdown": ("nt", ["shutdown", "/s", "/t", "5"]),
            "restart": ("nt", ["shutdown", "/r", "/t", "5"]),
            "sleep": ("nt", ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]),
        }
        posix_plan = {
            "shutdown": ["shutdown", "-h", "now"],
            "restart": ["reboot"],
            "sleep": ["systemctl", "suspend"],
        }
        try:
            if os.name == "nt":
                cmd = plan[action][1]
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                if action not in posix_plan:
                    return jsonify({"status": "error", "message": "Unknown command"}), 400
                subprocess.Popen(posix_plan[action], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            return jsonify({"status": "success"})
        except KeyError:
            return jsonify({"status": "error", "message": "Unknown command"}), 400
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    return app


# mutable holder so streams can observe shutdown without circular imports
server_state = {"running": False}


def run_server(app, port):
    """Bind-check then serve; sets server_state for stream generators."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("0.0.0.0", port))
    except OSError:
        probe.close()
        raise RuntimeError("端口 %d 已被占用" % port)
    probe.close()

    server_state["running"] = True
    from werkzeug.serving import make_server
    srv = make_server("0.0.0.0", port, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv
