@echo off
chcp 65001 >nul
rem Собирает Windows-версию в dist\DWG_Sheet_Scanner\DWG_Sheet_Scanner.exe.
rem Запускать на Windows из корня проекта двойным кликом или из cmd.
setlocal
cd /d "%~dp0"

py -3 -m venv .venv-build
if errorlevel 1 goto failed
call .venv-build\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 goto failed
python -m pip install -r requirements-build.txt
if errorlevel 1 goto failed
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onedir ^
  --windowed ^
  --name DWG_Sheet_Scanner ^
  --add-data "assets\GOST2304A.ttf;assets" ^
  --collect-all ezdxf ^
  --collect-all pymupdf ^
  main.py
if errorlevel 1 goto failed
copy /Y DISTRIBUTION.md dist\DWG_Sheet_Scanner\README.md >nul
copy /Y ИНСТРУКЦИЯ.txt dist\DWG_Sheet_Scanner\ИНСТРУКЦИЯ.txt >nul

endlocal
exit /b 0

:failed
echo Build failed. See the error above.
pause
endlocal
exit /b 1
