@echo off
setlocal

set "LAUNCHER=%~dp0scripts\deepseek-subagent.ps1"

if not exist "%LAUNCHER%" (
    echo ERROR: Skill launcher was not found:
    echo %LAUNCHER%
    echo.
    pause
    exit /b 2
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" prepare --json
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo prepare finished with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
