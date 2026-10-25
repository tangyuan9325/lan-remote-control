# -*- coding: utf-8 -*-
"""Claude-styled PyQt5 control panel for the RemoteControl 1.4 refactor.

Visual language mirrors the web console (cream #faf9f5 background, serif
titles, terracotta #d97757 accent, hairline borders, rounded cards) and is
applied through ONE global stylesheet instead of dozens of per-widget blocks.

PyInstaller fixes vs legacy packeg.bat flow:
  * no .spec editing step needed — hidden imports are declared in the spec
    (rc14.spec) and via --collect-all flags there;
  * icon resolved with resource_path() so it works from _MEIPASS;
  * uploads dir anchored to the exe location, not CWD.
"""

import sys

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QGroupBox, QFormLayout, QMessageBox,
    QStatusBar, QComboBox, QSlider, QCheckBox, QSystemTrayIcon, QAction,
    QMenu, QSizePolicy,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QIcon, QColor, QPalette

from .core import Config, VERSION, get_local_ip, resource_path, stop_all_workers
from .server import create_app, run_server, server_state

ACCENT = "#d97757"

STYLE_SHEET = """
QMainWindow, QDialog { background: #faf9f5; }
QWidget { color: #1f1e1b; font-family: "Segoe UI","Microsoft YaHei",sans-serif; font-size: 13px; }

QLabel#TitleLabel {
    font-family: Georgia,"SimSun",serif; font-size: 26px; font-weight: 600; color: #1f1e1b;
}
QLabel#SubLabel { color: #6e6a61; font-size: 12px; }
QLabel#StatusRunning { color: #3f7d5c; font-size: 15px; font-weight: 600; }
QLabel#StatusStopped { color: #c0392b; font-size: 15px; font-weight: 600; }
QLabel#LinkLabel { color: #c15f3e; font-weight: 600; font-size: 14px; }

QGroupBox {
    background: #ffffff; border: 1px solid #e8e3d9; border-radius: 12px;
    margin-top: 14px; padding: 16px 14px 12px 14px; font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 14px; top: 2px;
    padding: 2px 10px; background: #f5f1e8; border: 1px solid #e8e3d9;
    border-radius: 8px; color: #6e6a61; font-weight: 600;
}

QLineEdit, QComboBox {
    background: #ffffff; border: 1px solid #e8e3d9; border-radius: 8px;
    padding: 7px 10px; selection-background-color: #d97757;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid #d97757; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: #ffffff; border: 1px solid #e8e3d9; border-radius: 8px;
    selection-background-color: #f5f1e8; selection-color: #1f1e1b;
}

QPushButton {
    background: #ffffff; border: 1px solid #e8e3d9; border-radius: 10px;
    padding: 10px 22px; font-weight: 600;
}
QPushButton:hover { border: 1px solid #d97757; color: #c15f3e; }
QPushButton:pressed { background: #f5f1e8; }
QPushButton:disabled { color: #b8b2a6; background: #f2efe7; border: 1px solid #ece7dc; }

QPushButton#PrimaryBtn { background: #d97757; border: 1px solid #d97757; color: #ffffff; }
QPushButton#PrimaryBtn:hover { background: #c15f3e; }
QPushButton#PrimaryBtn:disabled { background: #e8dccd; color: #fdf9f3; }

QPushButton#DangerBtn { background: #ffffff; border: 1px solid #e8e3d9; color: #c0392b; }
QPushButton#DangerBtn:hover { border: 1px solid #c0392b; background: #fdf3f2; }

QCheckBox { spacing: 8px; }
QCheckBox::indicator {
    width: 18px; height: 18px; border-radius: 5px;
    border: 1px solid #cfc8ba; background: #ffffff;
}
QCheckBox::indicator:checked { background: #d97757; border: 1px solid #d97757; image: none; }

QSlider::groove:horizontal { height: 6px; background: #ece7dc; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #d97757; border-radius: 3px; }
QSlider::handle:horizontal {
    width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;
    background: #ffffff; border: 2px solid #d97757;
}

QStatusBar { background: #f5f1e8; color: #6e6a61; border-top: 1px solid #e8e3d9; }
QMessageBox { background: #faf9f5; }
"""


class FlaskThread(QThread):
    server_started = pyqtSignal(str, int)
    server_stopped = pyqtSignal()
    server_failed = pyqtSignal(str)

    def __init__(self, port=5000):
        super().__init__()
        self.port = port
        self.srv = None
        self._running = False

    def run(self):
        try:
            app = create_app(self.port)
            self.srv = run_server(app, self.port)
            self._running = True
            self.server_started.emit(get_local_ip(), self.port)
            while self._running:
                self.msleep(200)
        except Exception as e:
            self.server_failed.emit(str(e))
        finally:
            server_state["running"] = False
            self.server_stopped.emit()

    def stop(self):
        self._running = False
        if self.srv is not None:
            try:
                self.srv.shutdown()
            except Exception:
                pass
            self.srv = None


class SystemTrayIcon(QSystemTrayIcon):
    def __init__(self, icon_path, main_window=None):
        super().__init__(QIcon(icon_path), main_window)
        self.main_window = main_window
        menu = QMenu(main_window)
        for text, slot in (
            ("显示主窗口", self.show_main_window),
            ("启动服务", lambda: self._call("start_server")),
            ("停止服务", lambda: self._call("stop_server")),
            None,
            ("退出程序", lambda: self._call("quit_app")),
        ):
            if text is None:
                menu.addSeparator()
                continue
            act = QAction(text, main_window)
            act.triggered.connect(slot)
            menu.addAction(act)
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _call(self, method):
        if self.main_window:
            getattr(self.main_window, method)()

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger or reason == QSystemTrayIcon.DoubleClick:
            self.show_main_window()

    def show_main_window(self):
        if self.main_window:
            self.main_window.showNormal()
            self.main_window.raise_()
            self.main_window.activateWindow()


def _card_title(text):
    lbl = QLabel(text)
    lbl.setObjectName("TitleLabel")
    return lbl


class RemoteControlGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("远程控制被控端 · v" + VERSION)
        self.setMinimumSize(720, 640)
        self.resize(760, 680)
        self.setStyleSheet(STYLE_SHEET)
        self.flask_thread = None
        self._force_quit = False

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(28, 24, 28, 18)
        root.setSpacing(16)

        # ---- header ----
        head = QHBoxLayout()
        mark = QLabel("R")
        mark.setFixedSize(40, 40)
        mark.setAlignment(Qt.AlignCenter)
        mark.setStyleSheet(
            "background:#d97757;color:white;border-radius:12px;"
            "font-family:Georgia,serif;font-size:22px;font-weight:700;border:none;"
        )
        head.addWidget(mark)
        titles = QVBoxLayout()
        t1 = QLabel("远程控制被控端")
        t1.setObjectName("TitleLabel")
        t2 = QLabel("Claude 风格重构版 · 按需采集 · 空闲零占用")
        t2.setObjectName("SubLabel")
        titles.addWidget(t1)
        titles.addWidget(t2)
        head.addLayout(titles)
        head.addStretch(1)
        root.addLayout(head)

        # ---- server config ----
        srv_group = QGroupBox("服务器")
        srv_form = QFormLayout(srv_group)
        self.port_input = QLineEdit("5000")
        self.port_input.setPlaceholderText("1024 - 65535")
        self.port_input.setFixedWidth(160)
        srv_form.addRow("端口号", self.port_input)
        self.pwd_input = QLineEdit("")
        self.pwd_input.setEchoMode(QLineEdit.Password)
        self.pwd_input.setPlaceholderText("留空则不启用（推荐设置）")
        srv_form.addRow("访问密码", self.pwd_input)
        self.cmd_check = QCheckBox("允许远程命令执行（默认关闭，存在 RCE 风险）")
        srv_form.addRow(self.cmd_check)
        root.addWidget(srv_group)

        # ---- video settings ----
        vid_group = QGroupBox("画面串流")
        vid_form = QFormLayout(vid_group)
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems([
            "640x360 (省算力)", "800x600 (SVGA)", "1024x768 (XGA)",
            "1280x720 (HD)", "1280x800", "1600x900 (HD+)", "1920x1080 (Full HD)",
        ])
        self.resolution_combo.setCurrentIndex(3)
        vid_form.addRow("分辨率", self.resolution_combo)

        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["5 FPS (静态画面)", "10 FPS", "15 FPS (推荐)",
                                 "24 FPS", "30 FPS", "60 FPS"])
        self.fps_combo.setCurrentIndex(2)
        vid_form.addRow("帧率上限", self.fps_combo)

        qrow = QHBoxLayout()
        self.quality_slider = QSlider(Qt.Horizontal)
        self.quality_slider.setRange(10, 95)
        self.quality_slider.setValue(60)
        self.quality_label = QLabel("60%")
        self.quality_label.setFixedWidth(44)
        self.quality_slider.valueChanged.connect(lambda v: self.quality_label.setText("%d%%" % v))
        qrow.addWidget(self.quality_slider, 1)
        qrow.addWidget(self.quality_label)
        vid_form.addRow("JPEG 画质", qrow)

        hint = QLabel("提示：无观看者时采集线程自动休眠；画面静止时跳过编码（帧差检测）。")
        hint.setObjectName("SubLabel")
        hint.setWordWrap(True)
        vid_form.addRow(hint)
        root.addWidget(vid_group)

        # ---- features ----
        feat_group = QGroupBox("外设开关")
        feat_layout = QHBoxLayout(feat_group)
        self.camera_checkbox = QCheckBox("允许摄像头（观看时才采集）")
        self.mic_checkbox = QCheckBox("允许麦克风（监听时才采集）")
        feat_layout.addWidget(self.camera_checkbox)
        feat_layout.addWidget(self.mic_checkbox)
        feat_layout.addStretch(1)
        root.addWidget(feat_group)

        # ---- status ----
        st_group = QGroupBox("运行状态")
        st_layout = QVBoxLayout(st_group)
        self.status_label = QLabel("● 服务器未运行")
        self.status_label.setObjectName("StatusStopped")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.ip_label = QLabel("局域网地址：—")
        self.ip_label.setAlignment(Qt.AlignCenter)
        self.ip_label.setObjectName("SubLabel")
        self.url_label = QLabel("访问地址：—")
        self.url_label.setAlignment(Qt.AlignCenter)
        self.url_label.setObjectName("LinkLabel")
        self.url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        st_layout.addWidget(self.status_label)
        st_layout.addWidget(self.ip_label)
        st_layout.addWidget(self.url_label)
        root.addWidget(st_group)

        # ---- buttons ----
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        self.start_btn = QPushButton("启动服务器")
        self.start_btn.setObjectName("PrimaryBtn")
        self.start_btn.clicked.connect(self.start_server)
        self.stop_btn = QPushButton("停止服务器")
        self.stop_btn.setObjectName("DangerBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_server)
        self.minimize_btn = QPushButton("最小化到托盘")
        self.minimize_btn.clicked.connect(self.hide_to_tray)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.minimize_btn)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        sb = QStatusBar()
        self.setStatusBar(sb)
        sb.showMessage("就绪 — v" + VERSION)

        # tray
        self.tray_icon = None
        try:
            icon_file = resource_path("app.ico")
            self.tray_icon = SystemTrayIcon(icon_file, self)
            if not self.tray_icon.isNull():
                self.tray_icon.show()
            else:
                self.tray_icon = None
        except Exception as e:
            print("托盘不可用: %s" % e)
            self.tray_icon = None

        self.port_input.textChanged.connect(self.validate_port)
        self.validate_port(self.port_input.text())

    # ------------------------------------------------------------------
    def validate_port(self, text):
        try:
            ok = 1024 <= int(text) <= 65535
        except ValueError:
            ok = False
        self.start_btn.setEnabled(ok and self.flask_thread is None)
        return ok

    def get_video_settings(self):
        res_text = self.resolution_combo.currentText().split()[0]
        w, h = map(int, res_text.split("x"))
        fps = int(self.fps_combo.currentText().split()[0])
        quality = self.quality_slider.value()
        return (w, h), fps, quality

    def start_server(self):
        if self.flask_thread is not None:
            return
        if not self.validate_port(self.port_input.text()):
            QMessageBox.warning(self, "错误", "请输入有效的端口号 (1024-65535)")
            return
        resolution, fps, quality = self.get_video_settings()
        Config.resolution = resolution
        Config.fps = fps
        Config.jpeg_quality = quality
        Config.camera_enabled = self.camera_checkbox.isChecked()
        Config.microphone_enabled = self.mic_checkbox.isChecked()
        Config.enable_cmd = self.cmd_check.isChecked()
        Config.password = self.pwd_input.text().strip()

        for wdg in (self.port_input, self.pwd_input, self.resolution_combo,
                    self.fps_combo, self.quality_slider, self.camera_checkbox,
                    self.mic_checkbox, self.cmd_check):
            wdg.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_label.setText("◐ 正在启动…")
        self.status_label.setObjectName("StatusStopped")

        self.flask_thread = FlaskThread(int(self.port_input.text()))
        self.flask_thread.server_started.connect(self.on_server_started)
        self.flask_thread.server_stopped.connect(self.on_server_stopped)
        self.flask_thread.server_failed.connect(self.on_server_failed)
        self.flask_thread.start()

    def stop_server(self):
        if self.flask_thread is not None:
            self.flask_thread.stop()

    def on_server_failed(self, msg):
        self.statusBar().showMessage("启动失败: " + msg)
        QMessageBox.critical(self, "启动失败", "无法启动服务器：\n%s" % msg)

    def on_server_started(self, ip, port):
        self.status_label.setText("● 服务器运行中")
        self.status_label.setObjectName("StatusRunning")
        self.status_label.style().polish(self.status_label)
        self.ip_label.setText("局域网地址：%s" % ip)
        suffix = "/?password=…" if Config.password else ""
        self.url_label.setText("访问地址：http://%s:%d%s" % (ip, port, suffix))
        self.statusBar().showMessage(
            "已启动 http://%s:%d · %dx%d@%dfps q%d · 摄像头%s · 麦克风%s · CMD%s"
            % (ip, port, Config.resolution[0], Config.resolution[1], Config.fps,
               Config.jpeg_quality,
               "允许" if Config.camera_enabled else "禁用",
               "允许" if Config.microphone_enabled else "禁用",
               "开启(--enable-cmd)" if Config.enable_cmd else "禁用"))

    def on_server_stopped(self):
        self.status_label.setText("● 服务器已停止")
        self.status_label.setObjectName("StatusStopped")
        self.status_label.style().polish(self.status_label)
        self.ip_label.setText("局域网地址：—")
        self.url_label.setText("访问地址：—")
        self.statusBar().showMessage("服务器已停止")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        for wdg in (self.port_input, self.pwd_input, self.resolution_combo,
                    self.fps_combo, self.quality_slider, self.camera_checkbox,
                    self.mic_checkbox, self.cmd_check):
            wdg.setEnabled(True)
        self.flask_thread = None
        stop_all_workers()

    def hide_to_tray(self):
        self.hide()
        if self.tray_icon:
            self.tray_icon.showMessage("远程控制", "已最小化到系统托盘",
                                       QSystemTrayIcon.Information, 2000)

    def quit_app(self):
        self._force_quit = True
        self.close()

    def closeEvent(self, event):
        if self._force_quit or self.tray_icon is None:
            if self.flask_thread is not None:
                self.flask_thread.stop()
                self.flask_thread.wait(2500)
            stop_all_workers()
            if self.tray_icon:
                self.tray_icon.hide()
            event.accept()
        else:
            reply = QMessageBox.question(
                self, "确认", "关闭窗口将最小化到托盘，或选择退出程序？",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Cancel)
            if reply == QMessageBox.Cancel:
                event.ignore()
            elif reply == QMessageBox.Yes:   # minimize to tray
                self.hide_to_tray()
                event.ignore()
            else:                            # quit
                if self.flask_thread is not None:
                    self.flask_thread.stop()
                    self.flask_thread.wait(2500)
                stop_all_workers()
                self.tray_icon.hide()
                event.accept()


def run_gui():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("RemoteControl")
    pal = app.palette()
    pal.setColor(QPalette.Window, QColor("#faf9f5"))
    pal.setColor(QPalette.WindowText, QColor("#1f1e1b"))
    pal.setColor(QPalette.Base, QColor("#ffffff"))
    pal.setColor(QPalette.AlternateBase, QColor("#f5f1e8"))
    pal.setColor(QPalette.Text, QColor("#1f1e1b"))
    pal.setColor(QPalette.Button, QColor("#ffffff"))
    pal.setColor(QPalette.ButtonText, QColor("#1f1e1b"))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(pal)

    win = RemoteControlGUI()
    win.show()
    sys.exit(app.exec_())
