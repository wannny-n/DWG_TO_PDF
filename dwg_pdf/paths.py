"""Пути к ресурсам, одинаково работающие из исходников и сборки PyInstaller."""

from __future__ import annotations

from pathlib import Path
import sys


def resource_path(*parts: str) -> Path:
    """Возвращает путь к вложенному ресурсу приложения.

    PyInstaller распаковывает данные в ``sys._MEIPASS``. При обычном запуске
    ресурс лежит в корне репозитория, поэтому разработка и обе целевые ОС
    используют один и тот же вызов без привязки к текущей папке процесса.
    """

    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        root = Path(__file__).resolve().parent.parent
    return root.joinpath(*parts)
