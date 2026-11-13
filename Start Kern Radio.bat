@echo off
title Kern Radio
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1"
if errorlevel 1 (
  echo.
  echo Kern Radio could not finish starting. The error is shown above.
  pause
)
