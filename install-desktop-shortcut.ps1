$ErrorActionPreference = "Stop"
$ProjectDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonWindowed = Join-Path $ProjectDirectory ".venv\Scripts\pythonw.exe"
$Application = Join-Path $ProjectDirectory "desktop_app.py"

if (-not (Test-Path -LiteralPath $PythonWindowed -PathType Leaf)) {
    throw "Python environment not found. Run setup.cmd first."
}

$Desktop = [Environment]::GetFolderPath("Desktop")
$OldShortcutPath = Join-Path $Desktop "Local Voice Pilot.lnk"
$ShortcutPath = Join-Path $Desktop "Nova Voice Pilot.lnk"
if (Test-Path -LiteralPath $OldShortcutPath) {
    Remove-Item -LiteralPath $OldShortcutPath
}
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $PythonWindowed
$Shortcut.Arguments = '"' + $Application + '"'
$Shortcut.WorkingDirectory = $ProjectDirectory
$Shortcut.Description = "Start the fully local voice assistant"
$Shortcut.IconLocation = "$env:SystemRoot\System32\SndVol.exe,0"
$Shortcut.WindowStyle = 7
$Shortcut.Save()
Write-Host "Created desktop shortcut: $ShortcutPath"
