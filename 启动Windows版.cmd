@echo off
chcp 65001 >nul
start "" "%~dp0dist\windows\NovelGenerator\NovelGenerator.exe" --data-dir "%~dp0."
