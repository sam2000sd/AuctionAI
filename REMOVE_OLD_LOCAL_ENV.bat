@echo off
cd /d "%~dp0"
echo This deletes only the OLD .venv folder inside this extracted app folder, if it exists.
if exist ".venv" (
  rmdir /s /q ".venv"
  echo Deleted old local .venv.
) else (
  echo No old local .venv found.
)
pause
