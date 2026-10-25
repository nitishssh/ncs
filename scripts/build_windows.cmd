@echo off
rem Build dist\studio-utility\studio-utility.exe on the Windows PC, then run the bundle's self-test.
rem From the Mac:  ssh wednesday-tb studio-utility\scripts\build_windows.cmd
cd /d "%~dp0.."
.venv\Scripts\python -m pip install -q --disable-pip-version-check -e . pyinstaller || exit /b 1
.venv\Scripts\pyinstaller --noconfirm --log-level WARN installer\studio-utility.spec || exit /b 1
if exist "%USERPROFILE%\.studio-utility\self_test.txt" del "%USERPROFILE%\.studio-utility\self_test.txt"
start /wait "" dist\studio-utility\studio-utility.exe --self-test
type "%USERPROFILE%\.studio-utility\self_test.txt"
findstr /C:"FAILED" "%USERPROFILE%\.studio-utility\self_test.txt" >nul && (echo Self-test failed & exit /b 1)
rem Canon's SDK can't ship inside the app (licence); on this PC, put it where the installed app looks
if exist vendor\Windows\EDSDK_64\Dll\EDSDK.dll (
  robocopy vendor\Windows\EDSDK_64\Dll "%USERPROFILE%\.studio-utility\EDSDK" /E /NFL /NDL /NJH /NJS >nul
  echo Canon SDK available to the app: %USERPROFILE%\.studio-utility\EDSDK
)
if exist vendor\EDSDK\Windows\EDSDK_64\Dll\EDSDK.dll (
  robocopy vendor\EDSDK\Windows\EDSDK_64\Dll "%USERPROFILE%\.studio-utility\EDSDK" /E /NFL /NDL /NJH /NJS >nul
  echo Canon SDK available to the app: %USERPROFILE%\.studio-utility\EDSDK
)
echo Built: %CD%\dist\studio-utility\studio-utility.exe
