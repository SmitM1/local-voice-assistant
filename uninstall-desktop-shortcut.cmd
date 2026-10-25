@echo off
setlocal
powershell.exe -NoProfile -NonInteractive -Command "$desktop = [Environment]::GetFolderPath('Desktop'); @('Nova Voice Pilot.lnk', 'Local Voice Pilot.lnk') | ForEach-Object { $shortcut = Join-Path $desktop $_; if (Test-Path -LiteralPath $shortcut) { Remove-Item -LiteralPath $shortcut; Write-Host ('Removed desktop shortcut: ' + $shortcut) } }"
endlocal
