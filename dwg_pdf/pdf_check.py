"""Проверки PDF, не меняющие векторное содержимое исходного файла."""

from __future__ import annotations

from pathlib import Path
from typing import Callable
import tempfile

from .models import PdfCheck


def check_pdf(path: Path) -> PdfCheck:
    """Проверяет сигнатуру, страницы и доступность поискового текста.

    Отсутствие текста — предупреждение, а не повреждённый PDF: старые SHX-шрифты
    и некоторые proxy-объекты CAD корректно экспортируются как кривые.
    """

    if not path.is_file() or path.stat().st_size < 8:
        return PdfCheck(False, message="PDF не создан или имеет нулевой размер")
    with path.open("rb") as stream:
        signature = stream.read(5)
    if signature != b"%PDF-":
        return PdfCheck(False, message="Файл не начинается с сигнатуры PDF")

    try:
        import pymupdf
    except ImportError:
        pymupdf = None

    try:
        if pymupdf is not None:
            with pymupdf.open(path) as document:
                pages = len(document)
                text_found = any(page.get_text().strip() for page in document)
        else:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            pages = len(reader.pages)
            text_found = any((page.extract_text() or "").strip() for page in reader.pages)
        if pages == 0:
            return PdfCheck(False, message="PDF не содержит страниц")
        text_message = "текст найден" if text_found else "не найден извлекаемый текст"
        return PdfCheck(True, pages=pages, text_found=text_found, message=f"PDF корректен, {text_message}")
    except Exception as error:  # Библиотека возвращает разные классы ошибок по версиям.
        return PdfCheck(False, message=f"PDF не удалось разобрать: {error}")


def merge_pdfs(inputs: list[Path], destination: Path,
               progress: Callable[[float, str], None] | None = None) -> None:
    """Склеивает уже готовые PDF без повторной печати и растеризации."""

    # MuPDF copies PDF objects without interpreting thousands of CAD paths.
    # Preserve vector content and text and create the same per-DWG bookmarks.
    try:
        import pymupdf
    except ImportError:
        pymupdf = None
    if pymupdf is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".dwg_merge_", suffix=".pdf", dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
        try:
            with pymupdf.open() as output:
                bookmarks = []
                for index, source in enumerate(inputs):
                    if progress:
                        progress(index / max(1, len(inputs)) * 90, f"Объединение PDF: {index + 1}/{len(inputs)}")
                    bookmarks.append([1, source.stem, len(output) + 1])
                    with pymupdf.open(source) as document:
                        output.insert_pdf(document)
                output.set_toc(bookmarks)
                if progress:
                    progress(95, "Сохранение общего PDF")
                output.save(temporary, garbage=3, deflate=True)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        if progress:
            progress(100, "Общий PDF создан")
        return

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
