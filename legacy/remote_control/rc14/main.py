# -*- coding: utf-8 -*-
"""Entry point for the RemoteControl 1.4 refactor.

Usage:
    python -m rc14.main                 # GUI control panel (PyQt5)
    python -m rc14.main --headless      # server only, no GUI (Ctrl+C stops)
    python -m rc14.main --port 8080 --password s3cret --enable-cmd
"""

import sys
import time
import argparse


def build_parser():
    p = argparse.ArgumentParser(description="RemoteControl v1.4 (Claude-styled refactor)")
    p.add_argument("--port", type=int, default=5000, help="监听端口 (默认 5000)")
    p.add_argument("--password", default="", help="访问密码（强烈建议设置）")
    p.add_argument("--enable-cmd", action="store_true",
                   help="允许远程命令执行（默认禁用，RCE 风险）")
    p.add_argument("--fps", type=int, default=15, help="帧率上限 1-60 (默认 15)")
    p.add_argument("--quality", type=int, default=60, help="JPEG 画质 10-95 (默认 60)")
    p.add_argument("--resolution", default="1280x720", help="推流分辨率 WxH，长边≤1920")
    p.add_argument("--camera", action="store_true", help="允许客户端使用摄像头")
    p.add_argument("--mic", action="store_true", help="允许客户端使用麦克风")
    p.add_argument("--headless", action="store_true", help="无 GUI，仅运行服务器")
    return p


def apply_cli(args):
    from .core import Config
    try:
        w, h = (int(v) for v in args.resolution.lower().split("x"))
    except Exception:
        w, h = 1280, 720
    Config.resolution = (w, h)
    Config.fps = max(1, min(60, args.fps))
    Config.jpeg_quality = max(10, min(95, args.quality))
    Config.password = args.password or ""
    Config.enable_cmd = bool(args.enable_cmd)
    Config.camera_enabled = bool(args.camera)
    Config.microphone_enabled = bool(args.mic)


def run_headless(args):
    from .core import get_local_ip, VERSION
    from .server import create_app, run_server
    app = create_app(args.port)
    srv = run_server(app, args.port)
    print("RemoteControl %s 已启动: http://%s:%d" % (VERSION, get_local_ip(), args.port))
    if not args.password:
        print("⚠ 未设置访问密码，任何局域网设备均可控制本机！建议加 --password")
    if args.enable_cmd:
        print("⚠ 远程命令执行已开启 (--enable-cmd)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n正在停止…")
        srv.shutdown()


def main(argv=None):
    args = build_parser().parse_args(argv)
    apply_cli(args)

    if args.headless:
        run_headless(args)
        return 0

    try:
        from .gui import run_gui
    except ImportError as e:
        print("PyQt5 不可用 (%s)，自动回退到 headless 模式。" % e)
        run_headless(args)
        return 0
    run_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
