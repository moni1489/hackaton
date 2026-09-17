# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller-спек для сборки tray_agent.py в одиночный исполняемый файл.

Linux (запускать на Linux):
  pyinstaller build_linux.spec

Windows (запускать на Windows):
  pyinstaller build_windows.spec  ← отдельный спек, см. ниже

Или универсальная команда (запускать на целевой ОС):
  pyinstaller --onefile --noconsole --name SAM-VKO-Agent --icon icon.ico tray_agent.py
"""

import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

a = Analysis(
    ['tray_agent.py'],
    pathex=[str(Path(__file__).parent)],
    binaries=[],
    datas=[],
    hiddenimports=[
        'pystray._win32' if IS_WINDOWS else 'pystray._xorg',
        'PIL._tkinter_finder',
        'PIL.ImageFont',
        'PIL.ImageDraw',
        'tkinter',
        'tkinter.ttk',
        'tkinter.messagebox',
        'speedtest',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'pandas'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SAM-VKO-Agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # False = без консольного окна (GUI-режим)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='icon.ico',    # раскомментировать и добавить icon.ico для Windows
)
