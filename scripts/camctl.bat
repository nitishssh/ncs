@echo off
rem Double-click to open camctl on Windows.
cd /d "%~dp0.."
".venv\Scripts\camctl.exe" %*
