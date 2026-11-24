# Puts MemeBot on your Desktop and starts it.
# Run in PowerShell:
#   irm https://raw.githubusercontent.com/Fllipped-Bread/Crypto_meme/claude/brave-hopper-etvpla/install-desktop.ps1 | iex
# Run it again any time to update (your config.json, state.json and night.log are kept).
$ErrorActionPreference = "Stop"
$desktop = [Environment]::GetFolderPath("Desktop")
$dir = Join-Path $desktop "MemeBot"
$exe = Join-Path $dir "MemeBot.exe"
New-Item -ItemType Directory -Force -Path $dir | Out-Null

# stop a running copy so the file can be replaced
Get-Process MemeBot -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

Write-Host "Downloading MemeBot.exe ..."
$ProgressPreference = "SilentlyContinue"
Invoke-WebRequest "https://github.com/Fllipped-Bread/Crypto_meme/releases/download/latest/MemeBot.exe" -OutFile $exe
Unblock-File $exe

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path $desktop "MemeBot.lnk"))
$lnk.TargetPath = $exe
$lnk.WorkingDirectory = $dir
$lnk.Description = "MemeBot Terminal (paper trading)"
$lnk.Save()

Write-Host "Installed in $dir - starting it now. The dashboard opens in your browser."
Start-Process $exe -WorkingDirectory $dir
