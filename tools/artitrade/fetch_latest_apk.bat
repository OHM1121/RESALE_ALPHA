@echo off
rem Double-click: downloads the newest ARTITRADE APK into Desktop\artitrade apk (SHA-256 verified).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fetch_latest_apk.ps1"
pause
