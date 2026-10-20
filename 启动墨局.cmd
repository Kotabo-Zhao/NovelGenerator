@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "dist\windows-4.3.1\NovelGenerator\NovelGenerator.exe" (
  start "" "dist\windows-4.3.1\NovelGenerator\NovelGenerator.exe" --data-dir "%~dp0."
  exit /b 0
)
if exist "dist\windows\NovelGenerator\NovelGenerator.exe" (
  start "" "dist\windows\NovelGenerator\NovelGenerator.exe" --data-dir "%~dp0."
  exit /b 0
)
if not exist ".venv\Scripts\python.exe" (
  echo 首次使用请运行：python -m venv .venv
  echo 然后运行：.venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 run.py
if errorlevel 1 pause
