"""Консольный запуск автономного DWG -> PDF.

Скрипт полезен для серверной/пакетной обработки: GUI не требуется, nanoCAD не
запускается. Промежуточные DXF создаются в системной временной папке и удаляются
после каждого DWG.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from dwg_pdf.discovery import discover_dwg_files
from dwg_pdf.headless import HeadlessDwgConverter
from dwg_pdf.native_oda import NativeOdaPdfExporter
from dwg_pdf.pdf_check import check_pdf, merge_pdfs


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    """Описывает короткий, пригодный для командной строки интерфейс."""

    parser = argparse.ArgumentParser(description="Автономно преобразовать DWG в векторные PDF")
    parser.add_argument("sources", nargs="+", type=Path, help="DWG-файлы и/или папки с DWG")
    parser.add_argument("--output", required=True, type=Path, help="Каталог для PDF и отчётов")
    parser.add_argument("--oda", type=Path, help="Путь к бесплатному ODAFileConverter(.exe)")
    parser.add_argument("--native-exporter", type=Path, help="Необязательный ODA Drawings SDK PDF-экспортёр")
    parser.add_argument("--use-native-exporter", action="store_true", help="Явно использовать указанный нативный экспортёр")
    parser.add_argument("--font", action="append", type=Path, default=[], help="TTF/OTF для поискового слоя")
    parser.add_argument("--no-model-frames", action="store_true", help="Не печатать рамки ModelSpace")
    parser.add_argument("--no-layouts", action="store_true", help="Не печатать Layout")
    parser.add_argument("--merge", action="store_true", help="Создать общий DWG_Combined_autonomous.pdf")
    parser.add_argument("--overwrite", action="store_true", help="Перезаписать PDF с тем же именем")
    return parser.parse_args(arguments)


def output_path(output_dir: Path, source: Path, overwrite: bool) -> Path:
    """Даёт имени PDF контекст папки и не затирает чужой результат по умолчанию."""

    base = output_dir / f"{source.parent.name}__{source.stem}.pdf"
    if overwrite or not base.exists():
        return base
    index = 2
    while True:
        candidate = output_dir / f"{source.parent.name}__{source.stem}_{index}.pdf"
        if not candidate.exists():
            return candidate
        index += 1


def run(arguments: Sequence[str] | None = None) -> int:
    """Выполняет очередь, сохраняет ход после каждого DWG и возвращает код ОС."""

    options = parse_arguments(arguments)
    files = discover_dwg_files(
        [item for item in options.sources if item.is_file()],
        [item for item in options.sources if item.is_dir()],
    )
    if not files:
        print("DWG-файлы не найдены", flush=True)
        return 2

    output_dir = options.output.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir / "headless_batch_progress.json"
    report_path = output_dir / "headless_batch_report.json"

    def log(message: str) -> None:
        print(message, flush=True)

    if options.use_native_exporter:
        converter = NativeOdaPdfExporter(options.native_exporter, options.font, log, timeout_seconds=900)
        engine = "native ODA Drawings SDK PDF exporter"
    else:
        log("Бесплатный векторный экспорт без nanoCAD")
        converter = HeadlessDwgConverter(options.oda, options.font, log, timeout_seconds=900)
        engine = "free ODA File Converter + ezdxf + PyMuPDF"
    converter.validate()
    progress: list[dict[str, object]] = []
    for position, source in enumerate(files, 1):
        entry: dict[str, object] = {"position": position, "source": str(source), "status": "error"}
        try:
            result = converter.convert(
                source,
                output_path(output_dir, source, options.overwrite),
                include_model_frames=not options.no_model_frames,
                include_layouts=not options.no_layouts,
            )
            validation = check_pdf(result.output_pdf)
            if not validation.valid:
                raise RuntimeError(validation.message)
            entry.update(
                status="ok" if validation.text_found else "warning",
                output_pdf=str(result.output_pdf),
                sheets=result.sheet_count,
                formats=list(result.formats),
                searchable_text=validation.text_found,
                validation=validation.message,
            )
            log(f"OK {position}/{len(files)} {source.name}: {result.sheet_count} лист(а)")
        except Exception as error:
            entry["error"] = str(error)
            log(f"ERROR {position}/{len(files)} {source.name}: {error}")
        progress.append(entry)
        progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")

    successful_pdfs = [
        Path(str(item["output_pdf"]))
        for item in progress
        if item["status"] in {"ok", "warning"}
    ]
    combined_pdf: Path | None = None
    if options.merge and successful_pdfs:
        combined_pdf = output_dir / "DWG_Combined_autonomous.pdf"
        merge_pdfs(successful_pdfs, combined_pdf)

    report = {
        "engine": engine,
        "nanocad_used": False,
        "total_dwg": len(files),
        "succeeded": sum(item["status"] == "ok" for item in progress),
        "warnings": sum(item["status"] == "warning" for item in progress),
        "failed": sum(item["status"] == "error" for item in progress),
        "combined_pdf": str(combined_pdf) if combined_pdf else None,
        "files": progress,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in report if key != "files"}, ensure_ascii=False), flush=True)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run())
