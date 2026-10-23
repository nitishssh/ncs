@echo off
rem Install camctl on Windows and check it against the mock camera.
cd /d "%~dp0.."
if not exist .venv (
  py -3 -m venv .venv || exit /b 1
)
.venv\Scripts\python -m pip install -q --disable-pip-version-check -e . || exit /b 1
.venv\Scripts\python -c "from camctl import transports; t=transports.load('mock'); t.connect(); t.zoom(100); print('OK', t.info().model, t.status())"
