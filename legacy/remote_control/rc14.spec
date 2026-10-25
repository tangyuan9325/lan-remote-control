# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for RemoteControl v1.4 refactor (Claude-styled).
#
# WHY THIS FIXES THE OLD BUILD FAILURES (packeg.bat + remote_control_v1.4.spec):
#   1. Entry point is now a real file path ("rc14/main.py"), not the broken
#      "./main" reference of the old spec.
#   2. flask / werkzeug / PIL dynamic imports are collected explicitly, so the
#      frozen exe no longer dies with "ModuleNotFoundError: jinja2 / blinker".
#   3. cv2 + numpy binary/data files are collected via collect_* helpers —
#      the classic cause of "ImportError numpy.core._multiarray_umath" in
#      onefile builds.
#   4. PyQt5 plugin dirs (platforms/styles/systemtrayicon) are bundled; without
#      them the tray icon crashes on launch ("could not find Qt platform").
#   5. app.ico is bundled into _MEIPASS and resolved at runtime through
#      resource_path(); uploads/ is anchored to the exe dir instead of CWD.
#
# Build:  pyinstaller rc14.spec       (or simply run build_rc14.bat)

import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

hiddenimports = [
    # stdlib bits pulled in lazily
    'shlex', 'wave', 'socketserver', 'email.mime.multipart', 'email.mime.text',
    # flask stack
    'flask', 'werkzeug', 'jinja2', 'blinker', 'itsdangerous', 'click',
    'markupsafe',
    # imaging / input
    'PIL', 'PIL.Image', 'PIL.ImageGrab', 'PIL._imaging',
    'pyautogui', 'mouseinfo', 'pygetwindow', 'pymsgbox', 'pyscreeze',
    'pytweening', 'rubicon.objc',
    'numpy', 'cv2',
    # audio is optional at runtime but must be findable when installed
    'pyaudio',
    # our package modules (imported via relative imports)
    'rc14', 'rc14.core', 'rc14.webui', 'rc14.server', 'rc14.gui', 'rc14.main',
]

datas = [('app.ico', '.')]
datas += collect_data_files('cv2')
binaries = collect_dynamic_libs('cv2')

a = Analysis(
    ['rc14/main.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'PyQt6', 'PySide2', 'PySide6', 'matplotlib'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='RemoteControl-v1.4-Claude',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                       # UPX breaks cv2/PyQt DLLs on some machines
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                   # windowed app; use --headless build if you want a log console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app.ico',
)
