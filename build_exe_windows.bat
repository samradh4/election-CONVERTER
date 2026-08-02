@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Build Election PDF Converter EXE

echo =====================================================
echo   Election PDF Converter - Windows EXE Builder
echo =====================================================
echo.

where py >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python Launcher was not found.
  echo Install 64-bit Python 3.11 from python.org and enable "Add Python to PATH".
  pause
  exit /b 1
)

py -3.11 -c "import sys; print(sys.version)" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python 3.11 64-bit is required for a reliable Windows build.
  pause
  exit /b 1
)

set "TESSERACT_DIR="
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" set "TESSERACT_DIR=C:\Program Files\Tesseract-OCR"
if not defined TESSERACT_DIR if exist "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe" set "TESSERACT_DIR=C:\Program Files (x86)\Tesseract-OCR"
if not defined TESSERACT_DIR (
  for /f "delims=" %%I in ('where tesseract.exe 2^>nul') do if not defined TESSERACT_DIR set "TESSERACT_DIR=%%~dpI"
)
if not defined TESSERACT_DIR (
  echo [ERROR] Tesseract OCR is not installed on this Windows PC.
  echo Install Tesseract OCR first, including English language data, then run this file again.
  pause
  exit /b 1
)

if not exist "%TESSERACT_DIR%\tessdata\eng.traineddata" (
  echo [ERROR] eng.traineddata is missing from %TESSERACT_DIR%\tessdata
  pause
  exit /b 1
)
if not exist "%TESSERACT_DIR%\tessdata\hin.traineddata" (
  echo [INFO] Hindi OCR data is missing. Downloading hin.traineddata...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/hin.traineddata' -OutFile '%TESSERACT_DIR%\tessdata\hin.traineddata'"
  if errorlevel 1 (
    echo [ERROR] Could not download Hindi OCR data. Run this builder as Administrator or add hin.traineddata manually.
    pause
    exit /b 1
  )
)

if not exist ".buildvenv\Scripts\python.exe" (
  echo [1/5] Creating build environment...
  py -3.11 -m venv .buildvenv
  if errorlevel 1 goto :failed
)

echo [2/5] Installing build packages...
call ".buildvenv\Scripts\python.exe" -m pip install --upgrade pip
call ".buildvenv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 goto :failed

echo [3/5] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist release rmdir /s /q release
mkdir release

echo [4/5] Creating ElectionPDFConverter.exe...
set "TESSERACT_DIR=%TESSERACT_DIR%"
call ".buildvenv\Scripts\python.exe" -m PyInstaller --noconfirm --clean ElectionPDFConverter.spec
if errorlevel 1 goto :failed

xcopy /e /i /y "dist\ElectionPDFConverter" "release\ElectionPDFConverter" >nul

echo [5/5] Build completed.
echo.
echo Portable application:
echo   %CD%\release\ElectionPDFConverter\ElectionPDFConverter.exe
echo.
echo Give the client the whole ElectionPDFConverter folder,
echo or run build_installer_windows.bat to create one Setup.exe.
echo.
pause
exit /b 0

:failed
echo.
echo [ERROR] Build failed. Read the error shown above.
pause
exit /b 1
