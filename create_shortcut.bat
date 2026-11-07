@echo off
rem Creates a "Portfolio Manager" shortcut on the desktop (run again if you move or rename the folder).
setlocal
set "APPDIR=%~dp0"
set "APPDIR=%APPDIR:~0,-1%"
set "PYW="
for /f "delims=" %%P in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%P"
if not defined PYW (
    echo pythonw was not found. Install Python 3 from python.org or the Microsoft Store first.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$desk = [Environment]::GetFolderPath('Desktop');" ^
  "$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desk 'Portfolio Manager.lnk'));" ^
  "$lnk.TargetPath = $env:PYW;" ^
  "$lnk.Arguments = '\"' + (Join-Path $env:APPDIR 'portfolio_manager.py') + '\"';" ^
  "$lnk.WorkingDirectory = $env:APPDIR;" ^
  "$lnk.IconLocation = (Join-Path $env:APPDIR 'icon\icon.ico') + ',0';" ^
  "$lnk.Description = 'Portfolio Manager – investments, loans and own money';" ^
  "$lnk.Save();" ^
  "Write-Host ('Shortcut created: ' + (Join-Path $desk 'Portfolio Manager.lnk'))"
endlocal
