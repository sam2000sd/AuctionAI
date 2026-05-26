@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "AUCTIONAI_BASE=%LOCALAPPDATA%\AuctionAI"
echo This will delete the shared AuctionAI Python environment and Playwright browsers for this computer.
echo App files will not be deleted.
echo.
choice /C YN /M "Reset AuctionAI environment"
if errorlevel 2 exit /b 0
if exist "%AUCTIONAI_BASE%" rmdir /s /q "%AUCTIONAI_BASE%"
if exist ".venv" rmdir /s /q ".venv"
if exist ".setup_complete" del /q ".setup_complete"
if exist ".playwright_complete" del /q ".playwright_complete"
echo Reset complete. Run START_VISIBLE.bat again to rebuild setup for this computer.
pause
endlocal
