"""Windows COM-адаптер для последовательного запуска команд nanoCAD 5.1."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable


class NanoCadUnavailable(RuntimeError):
    """nanoCAD/COM недоступен или не готов к автоматизации."""


Log = Callable[[str], None]


class NanoCadComRunner:
    """Запускает один DWG в одном сеансе nanoCAD.

    `SendCommand` у COM nanoCAD асинхронен. Поэтому завершение определяется не
    задержкой, а появлением XML-отчёта, который плагин записывает только после
    завершения печати. Одновременная обработка намеренно запрещена: движок
    печати nanoCAD допускает только одну активную печать.
    """

    _PROG_IDS = ("nanoCAD.Application.5.1", "nanoCAD.Application.5", "nanoCAD.Application")

    def __init__(self, nanocad_exe: Path, plugin_dll: Path, log: Log) -> None:
        self._exe = nanocad_exe
        self._plugin = plugin_dll
        self._log = log

    def validate(self) -> None:
        if os.name != "nt":
            raise NanoCadUnavailable("Автозапуск nanoCAD 5.1 поддерживается только в Windows")
        if not self._exe.is_file():
            raise NanoCadUnavailable(f"Не найден nCad.exe: {self._exe}")
        if not self._plugin.is_file():
            raise NanoCadUnavailable(f"Не найдена DLL плагина: {self._plugin}")
        try:
            import win32com.client  # noqa: F401 - проверяем именно установленный COM-модуль.
        except ImportError as error:
            raise NanoCadUnavailable("Не установлен pywin32. Выполните: pip install -r requirements.txt") from error

    def run_active_document(self, source: Path, job_xml: Path, result_xml: Path, timeout: int) -> None:
        """Открывает DWG, загружает DLL и передаёт плагину путь задания."""

        self.validate()
        application = self._connect_or_start()
        application.Visible = True
        document = application.Documents.Open(str(source))
        try:
            # Префикс _ делает имена команд независимыми от русской локализации.
            self._send(document, '_.NETLOAD "{}"'.format(self._plugin))
            self._send(document, '_.DWG2PDF_EXPORT "{}"'.format(job_xml))
            self._wait_for_result(result_xml, timeout)
        finally:
            # Исходный файл никогда не сохраняется плагином. Закрываем без Save.
            try:
                document.Close(False)
            except Exception as error:
                self._log(f"Предупреждение: nanoCAD не закрыл документ: {error}")

    def _connect_or_start(self):
        import win32com.client

        for prog_id in self._PROG_IDS:
            try:
                application = win32com.client.GetActiveObject(prog_id)
                self._log(f"Подключение к запущенному nanoCAD через {prog_id}")
                return application
            except Exception:
                continue

        # Dispatch создаёт COM-сервер по зарегистрированному ProgID. Нельзя
        # угадывать CLSID или запускать EXE с несуществующими ключами /script.
        for prog_id in self._PROG_IDS:
            try:
                application = win32com.client.Dispatch(prog_id)
                self._log(f"Запущен nanoCAD через {prog_id}")
                return application
            except Exception:
                continue

        # На старых установках COM-сервер иногда регистрируется только после
        # обычного первого запуска nCad.exe. Пробуем ровно указанный в настройке
        # EXE, без shell и без неподтверждённых ключей командной строки.
        self._log("Запуск nCad.exe и ожидание регистрации COM-сервера")
        subprocess.Popen([str(self._exe)])
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            for prog_id in self._PROG_IDS:
                try:
                    return win32com.client.GetActiveObject(prog_id)
                except Exception:
                    continue
            time.sleep(0.5)
        raise NanoCadUnavailable(
            "COM-сервер nanoCAD 5.1 не зарегистрирован. Запустите nCad.exe один раз "
            "от имени текущего пользователя и проверьте установку nanoCAD."
        )

    def _send(self, document, command: str) -> None:
        self._log(f"nanoCAD: {command.split(' ', 1)[0]}")
        # SendCommand принимает CR/LF как завершение команды; передавать строки
        # через аргументы безопаснее, чем формировать временный SCR-файл.
        document.SendCommand(command + "\n")

    def _wait_for_result(self, result_xml: Path, timeout: int) -> None:
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            if result_xml.is_file() and result_xml.stat().st_size > 0:
                return
            time.sleep(0.25)
        raise TimeoutError(f"nanoCAD не создал XML-отчёт за {timeout} секунд: {result_xml}")
