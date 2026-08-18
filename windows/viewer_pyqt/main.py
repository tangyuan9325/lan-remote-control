#!/usr/bin/env python3
"""
LAN Remote Control - PyQt5 WebView Viewer
A clean, modern remote control interface using PyQt5 + QWebEngineView.

Usage:
  python main.py
"""

import os
import sys
from PyQt5.QtWidgets import QApplication, QMainWindow
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtCore import QUrl, Qt

from bridge import Bridge


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LAN Remote Control")
        self.resize(1100, 720)
        self.setMinimumSize(800, 560)

        # Bridge
        self.bridge = Bridge()

        # Web channel
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)

        # Web view
        self.view = QWebEngineView()
        self.view.page().setWebChannel(self.channel)

        # Load local HTML
        html_path = os.path.join(os.path.dirname(__file__), "web", "index.html")
        self.view.setUrl(QUrl.fromLocalFile(html_path))

        self.setCentralWidget(self.view)
        self.setStatusBar(None)  # clean look, status shown in-page


def main():
    # High DPI
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("LAN Remote Control")

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
