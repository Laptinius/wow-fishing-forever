@echo off
setlocal
cd /d "%~dp0"
set "WOWFISH_HOME=%~dp0runtime"
if not exist "%WOWFISH_HOME%\config" mkdir "%WOWFISH_HOME%\config"
if not exist "%WOWFISH_HOME%\config\config.toml" copy /y "%~dp0space-config.toml" "%WOWFISH_HOME%\config\config.toml" >nul
if not exist "%~dp0dist\bar4_2\wowfish-gui.exe" (
    echo Build missing: dist\bar4_2\wowfish-gui.exe
    pause
    exit /b 1
)
start "" "%~dp0dist\bar4_2\wowfish-gui.exe"
