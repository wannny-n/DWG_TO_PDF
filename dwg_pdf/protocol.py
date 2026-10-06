"""Устаревший XML-контракт заданий экспорта.

XML выбран вместо командной строки, чтобы пробелы и кириллица в путях не теряли
смысл, а результат можно было приложить к журналу приёмки.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class NativeResult:
    """Минимальный результат, который плагин обязан записать в XML."""

    success: bool
    source: Path
    output_pdf: Path | None
    sheet_count: int
    message: str


def write_job_xml(source: Path, output_pdf: Path, result_xml: Path, *, plotter: str,
                  include_model_frames: bool, include_layouts: bool) -> None:
    """Создаёт атомарный файл задания экспорта."""

    root = ET.Element("dwgPdfJob", {"version": "1"})
    ET.SubElement(root, "source", {"path": str(source)})
    ET.SubElement(root, "output", {"pdf": str(output_pdf), "resultXml": str(result_xml)})
    ET.SubElement(
        root,
        "options",
        {
            "pdfPlotter": plotter,
            "includeModelFrames": str(include_model_frames).lower(),
            "includeLayouts": str(include_layouts).lower(),
        },
    )

    result_xml.parent.mkdir(parents=True, exist_ok=True)
    temporary = result_xml.with_suffix(result_xml.suffix + ".tmp")
    ET.ElementTree(root).write(temporary, encoding="utf-8", xml_declaration=True)
    temporary.replace(result_xml.with_name(result_xml.stem + ".job.xml"))


def job_path_for(result_xml: Path) -> Path:
    """Возвращает имя задания, соответствующее имени будущего отчёта."""

    return result_xml.with_name(result_xml.stem + ".job.xml")


def read_native_result(path: Path) -> NativeResult:
    """Читает результат плагина и проверяет обязательные атрибуты."""

    root = ET.parse(path).getroot()
    if root.tag != "dwgPdfResult":
        raise ValueError(f"Неизвестный корневой XML-элемент: {root.tag}")

    success = root.get("success", "false").casefold() == "true"
    source_text = root.get("source")
    if not source_text:
        raise ValueError("В XML-результате отсутствует source")
    output_text = root.get("outputPdf")
    sheet_count = int(root.get("sheetCount", "0"))
    message = (root.findtext("message") or "").strip()
    return NativeResult(
        success=success,
        source=Path(source_text),
        output_pdf=Path(output_text) if output_text else None,
        sheet_count=sheet_count,
        message=message,
    )
