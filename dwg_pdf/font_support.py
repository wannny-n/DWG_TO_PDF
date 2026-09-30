"""Временное предоставление TTF-шрифтов процессу nanoCAD в Windows."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Callable, Iterable


Log = Callable[[str], None]


class FontResourceSession:
    """Добавляет TTF в таблицу шрифтов Windows только на время экспорта.

    Файл не копируется в системный каталог Fonts и не записывается в реестр.
    Такой режим достаточно безопасен для пакетной печати: nanoCAD получает
    шрифт до открытия DWG, а ресурс удаляется после завершения очереди.
    """

    def __init__(self, fonts: Iterable[Path], log: Log) -> None:
        self._fonts = tuple(Path(font) for font in fonts)
        self._log = log
        self._loaded: list[Path] = []

    def __enter__(self) -> "FontResourceSession":
        for font in self._fonts:
            if not font.is_file():
                raise FileNotFoundError(f"Не найден дополнительный шрифт: {font}")
            if font.suffix.casefold() not in {".ttf", ".otf"}:
                raise ValueError(f"Поддерживается TTF/OTF, а не: {font.name}")
        if not self._fonts:
            return self
        if os.name != "nt":
            raise RuntimeError("Временная загрузка TTF для nanoCAD доступна только в Windows")

        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        add_font = gdi32.AddFontResourceW
        add_font.argtypes = [ctypes.c_wchar_p]
        add_font.restype = ctypes.c_int
        for font in self._fonts:
            if add_font(str(font)) == 0:
                error = ctypes.get_last_error()
                self._remove_loaded(gdi32)
                raise OSError(error, f"Windows не загрузила шрифт {font.name}")
            self._loaded.append(font)
            self._log(f"Подключён TTF на время экспорта: {font.name}")
        self._notify_font_change()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if os.name != "nt" or not self._loaded:
            return
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self._remove_loaded(gdi32)
        self._notify_font_change()

    def _remove_loaded(self, gdi32) -> None:
        remove_font = gdi32.RemoveFontResourceW
        remove_font.argtypes = [ctypes.c_wchar_p]
        remove_font.restype = ctypes.c_bool
        for font in reversed(self._loaded):
            remove_font(str(font))
        self._loaded.clear()

    @staticmethod
    def _notify_font_change() -> None:
        """Сообщает уже открытым программам, что перечень шрифтов изменился."""

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        # HWND_BROADCAST, WM_FONTCHANGE, SMTO_ABORTIFHUNG; тайм-аут не даёт
        # зависшему стороннему окну задержать пакетную печать.
        result = ctypes.c_size_t()
        user32.SendMessageTimeoutW(0xFFFF, 0x001D, 0, 0, 0x0002, 1000, ctypes.byref(result))
