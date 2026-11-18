@echo off
setlocal
if "%~1"=="" (
  echo Drag one ARTITRADE Error or Research ZIP onto this CMD file.
  echo The original ZIP stays local. Only a small .DIAG.txt is generated.
  pause
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ARTITRADE_ZIP_CHECK.ps1" -ZipPath "%~1"
set "ARTITRADE_RESULT=%ERRORLEVEL%"
if not "%ARTITRADE_RESULT%"=="0" (
  echo ZIP diagnostic launch failed. No original ZIP was modified.
)
pause
exit /b %ARTITRADE_RESULT%
