#!/usr/bin/env bash
# Собирает переносимую Linux-версию в dist/DWG_Sheet_Scanner/.
# Запускать из корня проекта: bash build_linux.sh
set -euo pipefail

cd "$(dirname "$0")"
PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! "$PYTHON_BIN" -c "import tkinter"; then
  echo "Не найден tkinter. Установите python3-tk или передайте PYTHON_BIN с поддержкой Tk." >&2
  exit 1
fi
"$PYTHON_BIN" -m venv .venv-build
.venv-build/bin/python -m pip install --upgrade pip
.venv-build/bin/python -m pip install -r requirements-build.txt

# Некоторые переносимые Python (включая uv) держат Tcl/Tk рядом с интерпретатором,
# а не в системном пути загрузчика. Явно вкладываем эти библиотеки, чтобы
# готовый каталог запускался на Linux без установленного python3-tk.
TK_LIBRARY_DIR="$("$PYTHON_BIN" -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR"))')"
TK_BINARIES=()
for library in "$TK_LIBRARY_DIR"/libtcl*.so "$TK_LIBRARY_DIR"/libtcl*.so.* "$TK_LIBRARY_DIR"/libtk*.so "$TK_LIBRARY_DIR"/libtk*.so.*; do
  if [ -f "$library" ]; then
    TK_BINARIES+=(--add-binary "$library:.")
  fi
done
.venv-build/bin/python -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --windowed \
  --name DWG_Sheet_Scanner \
  --add-data "assets/GOST2304A.ttf:assets" \
  --collect-all ezdxf \
  --collect-all pymupdf \
  "${TK_BINARIES[@]}" \
  main.py
cp DISTRIBUTION.md dist/DWG_Sheet_Scanner/README.md
