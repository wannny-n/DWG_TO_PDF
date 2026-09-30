"""Проверки PDF, не меняющие векторное содержимое исходного файла."""

from __future__ import annotations

from pathlib import Path

from .models import PdfCheck


def check_pdf(path: Path) -> PdfCheck:
    """Проверяет сигнатуру, страницы и доступность поискового текста.

    Отсутствие текста — предупреждение, а не повреждённый PDF: старые SHX-шрифты
    и некоторые proxy-объекты CAD корректно экспортируются как кривые.
    """

    if not path.is_file() or path.stat().st_size < 8:
        return PdfCheck(False, message="PDF не создан или имеет нулевой размер")
    if path.read_bytes()[:5] != b"%PDF-":
        return PdfCheck(False, message="Файл не начинается с сигнатуры PDF")

    try:
        from pypdf import PdfReader
    except ImportError:
        return PdfCheck(True, message="PDF создан; для проверки текста установите pypdf")

    try:
        reader = PdfReader(str(path))
        pages = len(reader.pages)
        text_found = any((page.extract_text() or "").strip() for page in reader.pages)
        text_message = "текст найден" if text_found else "не найден извлекаемый текст"
        return PdfCheck(True, pages=pages, text_found=text_found, message=f"PDF корректен, {text_message}")
    except Exception as error:  # Библиотека возвращает разные классы ошибок по версиям.
        return PdfCheck(False, message=f"PDF не удалось разобрать: {error}")


def merge_pdfs(inputs: list[Path], destination: Path) -> None:
    """Склеивает уже готовые PDF без повторной печати и растеризации."""

    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as error:
        raise RuntimeError("Для объединения PDF установите зависимости: pip install -r requirements.txt") from error

    writer = PdfWriter()
    for input_pdf in inputs:
        reader = PdfReader(str(input_pdf))
        first_page_number = len(writer.pages)
        # Закладки не меняют графику и текст листов, но в общем PDF позволяют
        # перейти к изометриям конкретного исходного DWG одним кликом.
        writer.add_outline_item(input_pdf.stem, first_page_number)
        for page in reader.pages:
            writer.add_page(page)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as target:
        writer.write(target)
