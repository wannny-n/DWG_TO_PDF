"""Адаптер к нативному headless-экспортёру на ODA Drawings SDK.

Python намеренно не читает и не перерисовывает DWG в production-режиме. Он
создаёт задание для отдельного бинарного экспортёра, собранного с лицензируемым
ODA Drawings SDK, затем проверяет получившийся PDF. Так стили, CTB/STB, SHX/TTF
и сложные DWG-объекты остаются в нативном CAD-движке.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Callable, Iterable


Log = Callable[[str], None]


@dataclass(frozen=True)
class NativeOdaResult:
    """Подтверждённый результат одного запуска нативного экспортёра."""

    output_pdf: Path
    sheet_count: int
    formats: tuple[str, ...]


class NativeOdaPdfExporter:
    """Запускает внешний dwg_native_pdf_exporter по документированному JSON-протоколу.

    Сам бинарный экспортёр поставляется отдельно вместе с ODA Drawings SDK,
    поскольку SDK требует лицензию и не может быть заменён Python-библиотекой.
    Описание его аргументов и JSON-ответа находится в
    NATIVE_EXPORTER_PROTOCOL.md.
    """

    def __init__(
        self,
        exporter_exe: Path | None,
        font_files: Iterable[Path],
        log: Log,
        timeout_seconds: int = 900,
    ) -> None:
        self._exporter_exe = exporter_exe
        self._font_files = tuple(Path(font) for font in font_files)
        self._log = log
        self._timeout_seconds = timeout_seconds

    def validate(self) -> None:
        """Проверяет только наличие локальных компонентов, не открывая DWG."""

        if not self._exporter_exe or not self._exporter_exe.is_file():
            raise FileNotFoundError(
                "Для production-экспорта укажите dwg_native_pdf_exporter, "
                "собранный с ODA Drawings SDK. nanoCAD не требуется."
            )
        for font in self._font_files:
            if not font.is_file():
                raise FileNotFoundError(f"Не найден CAD-шрифт: {font}")

    def convert(
        self,
        source: Path,
        destination: Path,
        *,
        include_model_frames: bool,
        include_layouts: bool,
    ) -> NativeOdaResult:
        """Просит native-движок записать все листы одного DWG в один PDF."""

        if not source.is_file():
            raise FileNotFoundError(f"Не найден DWG: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        result_path = destination.with_suffix(".native-result.json")
        if result_path.exists():
            result_path.unlink()

        command = [
            str(self._exporter_exe),
            "--source",
            str(source),
            "--output",
            str(destination),
            "--result",
            str(result_path),
            "--include-layouts",
            "1" if include_layouts else "0",
            "--include-model-frames",
            "1" if include_model_frames else "0",
        ]
        for font in self._font_files:
            command.extend(("--font", str(font)))

        self._log(f"Нативный ODA PDF-экспорт: {source.name}")
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self._timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "нет диагностического сообщения").strip()
            raise RuntimeError(f"Нативный экспортёр завершился с кодом {completed.returncode}: {detail}")
        if not result_path.is_file():
            raise RuntimeError("Нативный экспортёр не создал .native-result.json")

        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            if not payload.get("success"):
                raise RuntimeError(str(payload.get("message") or "экспортёр сообщил об ошибке"))
            output_pdf = Path(str(payload["output_pdf"]))
            sheet_count = int(payload["sheet_count"])
            formats = tuple(str(item) for item in payload.get("formats", []))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Некорректный ответ нативного экспортёра: {error}") from error

        if output_pdf.resolve() != destination.resolve():
            raise RuntimeError("Нативный экспортёр вернул PDF не по запрошенному пути")
        if sheet_count < 1:
            raise RuntimeError("Нативный экспортёр не нашёл листов для печати")
        return NativeOdaResult(output_pdf=output_pdf, sheet_count=sheet_count, formats=formats)
