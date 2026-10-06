"""Небольшие модели данных, общие для интерфейса и пакетного запуска."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class JobStatus(str, Enum):
    """Состояние одного DWG в очереди."""

    QUEUED = "В очереди"
    RUNNING = "Обрабатывается"
    SUCCESS = "Готово"
    WARNING = "Готово с предупреждением"
    FAILED = "Ошибка"
    SKIPPED = "Пропущено"


@dataclass(frozen=True)
class RunSettings:
    """Настройки одного запуска.

    Все пути хранятся отдельно от исходных DWG: автономный конвертер не меняет
    оригинальный файл и записывает результат только в ``output_dir``.
    """

    output_dir: Path
    # Необязательный альтернативный путь: headless-бинарник, собранный с ODA
    # Drawings SDK. Основной режим программы остаётся бесплатным.
    native_exporter_exe: Optional[Path] = None
    # Путь к ODAFileConverter(.exe). Если поле пусто, программа ищет его через
    # переменную ODA_FILE_CONVERTER и PATH. Это основной бесплатный способ
    # прочитать закрытый формат DWG автономно.
    converter_exe: Optional[Path] = None
    # TTF/OTF встраивается в невидимый Unicode-слой для поиска в PDF. Это
    # особенно полезно, когда исходный CAD-шрифт экспортируется кривыми.
    font_files: tuple[Path, ...] = ()
    include_model_frames: bool = True
    include_layouts: bool = True
    # Сохраняем имя поля для совместимости с ранними настройками. True —
    # бесплатный ODA File Converter + ezdxf/PyMuPDF (режим по умолчанию).
    experimental_fallback: bool = True
    merge_to_one_pdf: bool = False
    combined_pdf: Optional[Path] = None
    wait_timeout_seconds: int = 600


@dataclass
class ConversionJob:
    """Один исходный DWG и результат его обработки."""

    source: Path
    position: int
    status: JobStatus = JobStatus.QUEUED
    message: str = "Ожидает запуска"
    sheet_count: int = 0
    output_pdf: Optional[Path] = None
    result_xml: Optional[Path] = None
    elapsed_seconds: Optional[float] = None


@dataclass(frozen=True)
class PdfCheck:
    """Результат проверки созданного PDF без изменения его содержимого."""

    valid: bool
    pages: int = 0
    text_found: bool = False
    message: str = ""


@dataclass
class RunSummary:
    """Итог очереди для строки состояния и журнала."""

    jobs: list[ConversionJob] = field(default_factory=list)

    @property
    def succeeded(self) -> int:
        return sum(job.status == JobStatus.SUCCESS for job in self.jobs)

    @property
    def warnings(self) -> int:
        return sum(job.status == JobStatus.WARNING for job in self.jobs)

    @property
    def failed(self) -> int:
        return sum(job.status == JobStatus.FAILED for job in self.jobs)
