"""Оркестратор очереди; в нём нет кода пользовательского интерфейса."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Iterable

from .models import ConversionJob, JobStatus, RunSettings, RunSummary
from .headless import HeadlessDwgConverter
from .native_oda import NativeOdaPdfExporter
from .pdf_check import check_pdf, merge_pdfs


Event = Callable[[ConversionJob], None]
Log = Callable[[str], None]


class ConversionService:
    """Последовательно выполняет задания и публикует изменения их состояния."""

    def __init__(self, settings: RunSettings, log: Log, on_change: Event) -> None:
        self._settings = settings
        self._log = log
        self._on_change = on_change
        self._free_mode = settings.experimental_fallback or settings.native_exporter_exe is None
        if self._free_mode:
            self._log("Бесплатный векторный экспорт без nanoCAD: ODA File Converter -> DXF -> PDF")
            self._runner = HeadlessDwgConverter(
                settings.converter_exe,
                settings.font_files,
                log,
                settings.wait_timeout_seconds,
            )
        else:
            self._runner = NativeOdaPdfExporter(
                settings.native_exporter_exe,
                settings.font_files,
                log,
                settings.wait_timeout_seconds,
            )

    def run(self, sources: Iterable[Path]) -> RunSummary:
        """Обрабатывает источники и возвращает полный, а не только успешный итог."""

        jobs = [ConversionJob(source=source, position=index + 1) for index, source in enumerate(sources)]
        summary = RunSummary(jobs=jobs)
        self._settings.output_dir.mkdir(parents=True, exist_ok=True)
        self._runner.validate()
        for job in jobs:
            self._convert_one(job)

        self._merge_if_requested(summary)
        return summary

    def _convert_one(self, job: ConversionJob) -> None:
        started = time.monotonic()
        job.status = JobStatus.RUNNING
        job.message = (
            "Бесплатный векторный экспорт без nanoCAD"
            if self._free_mode
            else "Нативный автономный экспорт без nanoCAD"
        )
        self._changed(job)

        output_pdf = self._unique_output_path(job.source)
        try:
            result = self._runner.convert(
                job.source,
                output_pdf,
                include_model_frames=self._settings.include_model_frames,
                include_layouts=self._settings.include_layouts,
            )
            job.sheet_count = result.sheet_count
            job.output_pdf = result.output_pdf
            check = check_pdf(result.output_pdf)
            if not check.valid:
                raise RuntimeError(check.message)
            job.status = JobStatus.SUCCESS if check.text_found else JobStatus.WARNING
            formats = ", ".join(result.formats)
            job.message = f"{result.sheet_count} лист(а): {formats}; {check.message}"
        except Exception as error:
            job.status = JobStatus.FAILED
            job.message = str(error)
            self._log(f"Ошибка {job.source.name}: {error}")
        finally:
            job.elapsed_seconds = round(time.monotonic() - started, 2)
            self._changed(job)

    def _unique_output_path(self, source: Path) -> Path:
        """Убирает коллизии одинаковых имён DWG из разных папок."""

        base = self._settings.output_dir / f"{source.stem}.pdf"
        if not base.exists():
            return base
        index = 2
        while True:
            candidate = self._settings.output_dir / f"{source.stem}_{index}.pdf"
            if not candidate.exists():
                return candidate
            index += 1

    def _merge_if_requested(self, summary: RunSummary) -> None:
        if not self._settings.merge_to_one_pdf:
            return
        inputs = [job.output_pdf for job in summary.jobs if job.output_pdf and job.status in {JobStatus.SUCCESS, JobStatus.WARNING}]
        if not inputs:
            self._log("Общий PDF не создан: нет успешных файлов")
            return
        destination = self._settings.combined_pdf or (self._settings.output_dir / "DWG_Combined.pdf")
        try:
            merge_pdfs(inputs, destination)
            self._log(f"Создан общий PDF: {destination}")
        except Exception as error:
            self._log(f"Не удалось объединить PDF: {error}")

    def _changed(self, job: ConversionJob) -> None:
        self._on_change(job)
