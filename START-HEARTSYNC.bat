@echo off
title HeartSync (local)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\run-local.ps1"
echo.
pause
