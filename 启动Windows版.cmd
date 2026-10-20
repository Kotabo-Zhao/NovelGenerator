@echo off
chcp 65001 >nul
if exist "%~dp0dist\windows-4.2.0\NovelGenerator\NovelGenerator.exe" (
  start "" "%~dp0dist\windows-4.2.0\NovelGenerator\NovelGenerator.exe" --data-dir "%~dp0."
  exit /b 0
)
start "" "%~dp0dist\windows\NovelGenerator\NovelGenerator.exe" --data-dir "%~dp0."
