# -*- mode: python ; coding: utf-8 -*-
import os, glob

DJVU = os.path.expanduser(r"~\scoop\apps\djvulibre\current")
djvu_files = [(f, "djvulibre") for f in
              [os.path.join(DJVU, n) for n in ("ddjvu.exe", "djvused.exe", "djvudump.exe")]
              + [f for f in glob.glob(os.path.join(DJVU, "*.dll")) if not os.path.basename(f).startswith("Qt5")]]

a = Analysis(
    ['djvu_reader.py'],
    pathex=[],
    binaries=[],
    datas=djvu_files + [('DjvuReader.ico', '.')],
    hiddenimports=['pymupdf'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt6.QtWebEngineCore', 'PyQt6.QtWebEngineWidgets', 'PyQt6.QtQml', 'PyQt6.QtQuick', 'PyQt6.QtQuick3D', 'PyQt6.QtMultimedia', 'PyQt6.QtMultimediaWidgets', 'PyQt6.QtNetwork', 'PyQt6.QtSql', 'PyQt6.QtPdf', 'PyQt6.QtCharts', 'PyQt6.Qt3DCore', 'PyQt6.QtDataVisualization', 'PyQt6.QtTest', 'PyQt6.QtBluetooth', 'PyQt6.QtSerialPort', 'PyQt6.QtPositioning', 'PyQt6.QtSensors', 'PyQt6.QtNfc', 'tkinter', 'numpy', 'pandas', 'matplotlib'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DjvuReader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon='DjvuReader.ico',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='DjvuReader',
)
