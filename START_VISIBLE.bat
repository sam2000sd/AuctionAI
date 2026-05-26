@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Auction Intelligence

echo Auction Intelligence launcher
echo.

REM Keep the Python environment OUTSIDE the app folder so deleting/updating the app is fast.
set "AUCTIONAI_BASE=%LOCALAPPDATA%\AuctionAI"
set "AUCTIONAI_ENV=%AUCTIONAI_BASE%\venv"
set "PLAYWRIGHT_BROWSERS_PATH=%AUCTIONAI_BASE%\playwright-browsers"
set "SETUP_MARKER=%AUCTIONAI_BASE%\setup_complete.txt"
set "PLAYWRIGHT_MARKER=%AUCTIONAI_BASE%\playwright_complete.txt"
if not exist "%AUCTIONAI_BASE%" mkdir "%AUCTIONAI_BASE%"

set PYTHON_CMD=
py -3.12 --version >nul 2>&1
if %errorlevel%==0 set PYTHON_CMD=py -3.12

if "%PYTHON_CMD%"=="" (
  py -3.11 --version >nul 2>&1
  if %errorlevel%==0 set PYTHON_CMD=py -3.11
)

if "%PYTHON_CMD%"=="" (
  python --version >nul 2>&1
  if %errorlevel%==0 set PYTHON_CMD=python
)

if "%PYTHON_CMD%"=="" (
  echo ERROR: Python not found. Install Python 3.11 or 3.12, then run this again.
  pause
  exit /b 1
)

echo Using Python:
%PYTHON_CMD% --version

echo Environment folder: %AUCTIONAI_ENV%
echo Browser folder: %PLAYWRIGHT_BROWSERS_PATH%

if exist "%AUCTIONAI_ENV%" (
  "%AUCTIONAI_ENV%\Scripts\python.exe" -m pip --version >nul 2>&1
  if errorlevel 1 (
    echo Broken shared environment found. Removing it so setup can rebuild cleanly...
    rmdir /s /q "%AUCTIONAI_ENV%"
    if exist "%SETUP_MARKER%" del /q "%SETUP_MARKER%"
    if exist "%PLAYWRIGHT_MARKER%" del /q "%PLAYWRIGHT_MARKER%"
  )
)

if not exist "%AUCTIONAI_ENV%\Scripts\python.exe" (
  echo Creating shared local environment for this computer...
  %PYTHON_CMD% -m venv "%AUCTIONAI_ENV%"
  if errorlevel 1 (
    echo ERROR: Could not create environment.
    pause
    exit /b 1
  )
  if exist "%SETUP_MARKER%" del /q "%SETUP_MARKER%"
  if exist "%PLAYWRIGHT_MARKER%" del /q "%PLAYWRIGHT_MARKER%"
)

set "PY=%AUCTIONAI_ENV%\Scripts\python.exe"
set NEED_SETUP=0
if not exist "%SETUP_MARKER%" set NEED_SETUP=1

if "%NEED_SETUP%"=="0" (
  "%PY%" -c "import streamlit, pandas, requests, bs4, lxml, playwright" >nul 2>&1
  if errorlevel 1 set NEED_SETUP=1
)

if "%NEED_SETUP%"=="1" (
  echo Installing/updating required packages for this computer. This happens on first run only.
  "%PY%" -m ensurepip --upgrade >nul 2>&1
  "%PY%" -m pip install --upgrade pip setuptools wheel
  if errorlevel 1 (
    echo ERROR: pip setup failed.
    pause
    exit /b 1
  )
  "%PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo ERROR: Package install failed.
    pause
    exit /b 1
  )
  echo setup complete> "%SETUP_MARKER%"
) else (
  echo Existing shared environment looks good. Skipping package reinstall.
)

if not exist "%PLAYWRIGHT_MARKER%" (
  echo Installing Playwright Chromium for this computer. This happens on first run only.
  "%PY%" -m playwright install chromium
  if errorlevel 1 (
    echo WARNING: Playwright Chromium install failed. AC scraping may not work until fixed.
  ) else (
    echo playwright complete> "%PLAYWRIGHT_MARKER%"
  )
) else (
  echo Playwright Chromium already marked installed. Skipping browser install.
)

echo.
echo Starting Auction Intelligence...
"%PY%" -m streamlit run app\Auction_Intelligence.py

endlocal
