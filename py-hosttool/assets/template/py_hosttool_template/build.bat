@echo off
setlocal
cd /d "%~dp0"
if not defined PYTHON_EXE set "PYTHON_EXE=python"
"%PYTHON_EXE%" -m pytest -q -p no:cacheprovider
if errorlevel 1 exit /b 1
if exist "build" rmdir /s /q "build"
if exist "release" rmdir /s /q "release"
"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean --distpath "release" --workpath "build" HostTool.spec
if errorlevel 1 exit /b 1
if not exist "release\{{EXE_NAME}}\{{EXE_NAME}}.exe" exit /b 1
"release\{{EXE_NAME}}\{{EXE_NAME}}.exe" --smoke-test
if errorlevel 1 exit /b 1
endlocal
