@echo off
setlocal
cd /d "%~dp0"
set "LVA_TARGET=%CD%\start.cmd"
set "LVA_LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Local Voice Assistant.lnk"
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LVA_LINK); $s.TargetPath=$env:LVA_TARGET; $s.WorkingDirectory=(Split-Path -Parent $env:LVA_TARGET); $s.WindowStyle=7; $s.Save()"
if errorlevel 1 exit /b 1
echo Installed current-user startup shortcut:
echo %LVA_LINK%
