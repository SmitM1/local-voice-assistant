@echo off
setlocal
set "LVA_LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Local Voice Assistant.lnk"
if exist "%LVA_LINK%" del /q "%LVA_LINK%"
if exist "%LVA_LINK%" (
  echo Could not remove startup shortcut.
  exit /b 1
)
echo Startup shortcut removed.
