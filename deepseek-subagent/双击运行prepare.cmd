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

echo [1/2] Configuring persistent Codex sandbox access for DeepSeek handoffs...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" bootstrap --json
if errorlevel 1 (
    set "EXIT_CODE=%ERRORLEVEL%"
    echo.
    echo ERROR: Persistent sandbox configuration failed with exit code %EXIT_CODE%.
    pause
    exit /b %EXIT_CODE%
)

echo.
echo [2/2] Configuring the managed Multi-Agent V1 route...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" repair --json
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo Initialization completed successfully.
    echo These settings are persistent; you do not need to run this file before every Codex launch.
    echo Fully exit Codex now, restart it, and create a NEW conversation before using DeepSeek subagents.
) else (
    echo ERROR: Multi-Agent V1 configuration failed with exit code %EXIT_CODE%.
)
pause
exit /b %EXIT_CODE%
