"""Оркестратор очереди; в нём нет кода пользовательского интерфейса."""

from __future__ import annotations

import time
import tempfile
from pathlib import Path
from typing import Callable, Iterable

from .models import ConversionJob, JobStatus, RunSettings, RunSummary
from .headless import HeadlessDwgConverter
from .native_oda import NativeOdaPdfExporter
from .pdf_check import check_pdf, merge_pdfs


Event = Callable[[ConversionJob], None]
Log = Callable[[str], None]
Progress = Callable[[float, str], None]


class ConversionService:
    """Последовательно выполняет задания и публикует изменения их состояния."""

    def __init__(self, settings: RunSettings, log: Log, on_change: Event,
                 progress: Progress | None = None) -> None:
        self._settings = settings
        self._log = log
        self._on_change = on_change
        self._work_dir = settings.output_dir
        self._progress = progress
        self._progress_value = 0.0
        self._total_jobs = 1
        self._active_position = 1
        self._conversion_weight = 95.0 if settings.merge_to_one_pdf else 100.0
        self._free_mode = settings.experimental_fallback or settings.native_exporter_exe is None
        if self._free_mode:
            self._log("Бесплатный векторный экспорт: ODA File Converter -> DXF -> PDF")
            self._runner = HeadlessDwgConverter(
                settings.converter_exe,
                settings.font_files,
                log,
                settings.wait_timeout_seconds,
                progress=self._engine_progress,
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
        self._total_jobs = max(1, len(jobs))
        self._progress_value = 0.0
        self._report(0, "Проверка настроек")
        self._settings.output_dir.mkdir(parents=True, exist_ok=True)
        self._runner.validate()
        if self._settings.merge_to_one_pdf:
            with tempfile.TemporaryDirectory(prefix="dwg_pdf_merge_") as temporary:
                self._work_dir = Path(temporary)
                try:
                    for job in jobs:
                        self._convert_one(job)
                    merged = self._merge_if_requested(summary)
                    if merged:
                        for job in jobs:
                            if job.status in {JobStatus.SUCCESS, JobStatus.WARNING}:
                                job.output_pdf = merged
                                self._changed(job)
                finally:
                    self._work_dir = self._settings.output_dir
        else:
            for job in jobs:
                self._convert_one(job)
        self._report(100, "Обработка завершена")
        return summary

    def _report(self, percent: float, stage: str) -> None:
        self._progress_value = max(self._progress_value, min(100.0, percent))
        if self._progress:
            self._progress(self._progress_value, stage)

    def _engine_progress(self, percent: float, stage: str) -> None:
        # Leave room for checking the file and for merging the queue.
        fraction = (self._active_position - 1 + min(98, percent) / 100) / self._total_jobs
        self._report(fraction * self._conversion_weight,
                     f"DWG {self._active_position}/{self._total_jobs} — {stage}")

    def _convert_one(self, job: ConversionJob) -> None:
        started = time.monotonic()
        self._active_position = job.position
        self._engine_progress(0, f"Обработка {job.source.name}")
        job.status = JobStatus.RUNNING
        job.message = (
            "Бесплатный векторный экспорт"
            if self._free_mode
            else "Нативный автономный экспорт"
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
            self._engine_progress(98, "Проверка PDF")
            check = check_pdf(result.output_pdf)
            if not check.valid:
                raise RuntimeError(check.message)
            warnings = getattr(result, "warnings", ())
            job.status = JobStatus.SUCCESS if check.text_found and not warnings else JobStatus.WARNING
            formats = ", ".join(result.formats)
            job.message = f"{result.sheet_count} лист(а): {formats}; {check.message}"
            if warnings:
                job.message += "; " + "; ".join(warnings)
        except Exception as error:
            job.status = JobStatus.FAILED
            job.message = str(error)
            self._log(f"Ошибка {job.source.name}: {error}")
        finally:
            job.elapsed_seconds = round(time.monotonic() - started, 2)
            self._changed(job)
            self._report(job.position / self._total_jobs * self._conversion_weight,
                         f"DWG {job.position}/{self._total_jobs} — {job.status.value}")

    def _unique_output_path(self, source: Path) -> Path:
        """Убирает коллизии одинаковых имён DWG из разных папок."""

        base = self._work_dir / f"{source.stem}.pdf"
        if not base.exists():
            return base
        index = 2
        while True:
            candidate = self._work_dir / f"{source.stem}_{index}.pdf"
            if not candidate.exists():
                return candidate
            index += 1

    def _merge_if_requested(self, summary: RunSummary) -> Path | None:
        if not self._settings.merge_to_one_pdf:
            return
        inputs = [job.output_pdf for job in summary.jobs if job.output_pdf and job.status in {JobStatus.SUCCESS, JobStatus.WARNING}]
        if not inputs:
            self._log("Общий PDF не создан: нет успешных файлов")
            return
        destination = self._settings.combined_pdf or (self._settings.output_dir / "DWG_Combined.pdf")
        try:
            self._report(95, "Объединение в общий PDF")
            merge_pdfs(inputs, destination,
                       progress=lambda percent, stage: self._report(95 + percent * 0.04, stage))
            self._log(f"Создан общий PDF: {destination}")
            return destination
        except Exception as error:
            raise RuntimeError(f"Не удалось объединить PDF: {error}") from error

    def _changed(self, job: ConversionJob) -> None:
        self._on_change(job)
