# -*- mode: python ; coding: utf-8 -*-
"""Windows onefile release for MasterController v1.4.

PyInstaller's native PySide6 hooks collect the Qt libraries and platform
plugins used by the application.  Do not use collect_all() here: in this Conda
environment it also pulls Conda's ICU DLLs into the application root, where
they take precedence over Windows' compatible ICU runtime and prevent QtCore
from loading.
"""

import os

from PyInstaller.building.datastruct import TOC


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['serial.tools.list_ports'],
    hookspath=[],
    excludes=[],
)

# Qt 6.11.1 loads successfully with the Windows ICU runtime.  The two Conda
# ICU binaries below are discovered transitively by PyInstaller but are not a
# compatible, complete runtime in a frozen bundle; keeping them causes
# QtCore.pyd to fail before the GUI starts.
_conflicting_conda_icu = {'icuuc.dll', 'icudt73.dll'}
a.binaries = TOC(
    entry for entry in a.binaries
    if os.path.basename(entry[0]).lower() not in _conflicting_conda_icu
)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MasterController_v1.4',
    console=False,
)
