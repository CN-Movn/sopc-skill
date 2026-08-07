@echo off
setlocal
set PYTHON_EXE=python
if exist "build" rmdir /s /q "build"
if exist "release" rmdir /s /q "release"
"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean --distpath "release" --workpath "build" HostTool.spec
endlocal
