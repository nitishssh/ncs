@echo off
rem Build dist\camctl\camctl.exe on the Windows PC, then run the bundle's self-test.
rem From the Mac:  ssh wednesday-tb camctl\scripts\build_windows.cmd
cd /d "%~dp0.."
.venv\Scripts\python -m pip install -q --disable-pip-version-check -e . pyinstaller || exit /b 1
.venv\Scripts\pyinstaller --noconfirm --log-level WARN installer\camctl.spec || exit /b 1
if exist "%USERPROFILE%\.camctl\self_test.txt" del "%USERPROFILE%\.camctl\self_test.txt"
start /wait "" dist\camctl\camctl.exe --self-test
type "%USERPROFILE%\.camctl\self_test.txt"
echo Built: %CD%\dist\camctl\camctl.exe
