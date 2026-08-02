@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Build Election PDF Converter Installer

if not exist "release\ElectionPDFConverter\ElectionPDFConverter.exe" (
  echo Portable EXE is not built yet. Starting EXE build first...
  call build_exe_windows.bat
  if errorlevel 1 exit /b 1
)

set "ISCC="
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
if not defined ISCC (
  echo [ERROR] Inno Setup 6 is not installed.
  echo Install Inno Setup 6, then run this file again.
  pause
  exit /b 1
)

"%ISCC%" "installer\ElectionPDFConverter.iss"
if errorlevel 1 (
  echo [ERROR] Installer build failed.
  pause
  exit /b 1
)

echo.
echo Installer created:
echo   %CD%\release\ElectionPDFConverter_Setup.exe
echo.
echo Give only this Setup.exe to the client.
pause
