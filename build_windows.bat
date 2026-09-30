@echo off
rem Собирает Windows-версию в dist\DWG_Sheet_Scanner\DWG_Sheet_Scanner.exe.
rem Запускать на Windows из корня проекта двойным кликом или из cmd.
setlocal
cd /d "%~dp0"

py -3 -m venv .venv-build
call .venv-build\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onedir ^
  --windowed ^
  --name DWG_Sheet_Scanner ^
  --add-data "task\GOST2304A.ttf;task" ^
  --collect-all ezdxf ^
  --collect-all pymupdf ^
  main.py
copy /Y DISTRIBUTION.md dist\DWG_Sheet_Scanner\README.md >nul

endlocal
