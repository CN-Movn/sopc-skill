# -*- mode: python ; coding: utf-8 -*-
"""Controlled Windows onedir build for {{APP_NAME}}."""

from PyInstaller.building.datastruct import TOC


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=["serial.tools.list_ports"],
    hookspath=[],
    excludes=[],
)

# Add only environment-specific conflict filters that have been reproduced and
# documented. Do not replace this with collect_all("PySide6").
a.binaries = TOC(entry for entry in a.binaries)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="{{EXE_NAME}}",
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="{{EXE_NAME}}",
)
