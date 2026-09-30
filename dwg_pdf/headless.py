"""Автономный конвейер DWG -> векторный PDF.

Модуль намеренно не использует COM, nanoCAD или окно CAD-программы. ODA File
Converter читает DWG в отдельном процессе и создаёт временный DXF, а ezdxf и
PyMuPDF строят PDF. Исходный DWG при этом не изменяется.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Iterable


Log = Callable[[str], None]


@dataclass(frozen=True)
class HeadlessResult:
    """Результат обработки одного исходного файла."""

    output_pdf: Path
    sheet_count: int
    formats: tuple[str, ...]


@dataclass(frozen=True)
class _Sheet:
    """Один лист, который нужно добавить в итоговый PDF."""

    title: str
    width_mm: float
    height_mm: float
    format_name: str
    layout: Any | None = None
    frame: Any | None = None


@dataclass(frozen=True)
class _SearchText:
    """Текст из DWG и его положение в координатах исходного листа."""

    value: str
    x: float
    y: float
    height: float
    scope: str


class HeadlessDwgConverter:
    """Преобразует один DWG без запуска nanoCAD.

    Для чтения закрытого формата DWG используется установленный пользователем
    ODA File Converter. Это отдельная утилита без CAD-интерфейса. Рендеринг PDF
    выполняется локально, поэтому обработка не зависит от принтера Windows.
    """

    _SHEET_BLOCK = re.compile(r"(?:^|[^A-ZА-Я0-9])(?:A|А)\s*([0-4])", re.IGNORECASE)
    _PAPER_SIZES = {
        "A0": (841.0, 1189.0),
        "A1": (594.0, 841.0),
        "A2": (420.0, 594.0),
        "A3": (297.0, 420.0),
        "A4": (210.0, 297.0),
    }

    def __init__(
        self,
        converter_exe: Path | None,
        font_files: Iterable[Path],
        log: Log,
        timeout_seconds: int = 600,
    ) -> None:
        self._requested_converter = converter_exe
        self._font_files = tuple(Path(path) for path in font_files)
        self._log = log
        self._timeout_seconds = timeout_seconds

    def validate(self) -> None:
        """Проверяет зависимости до помещения задания в очередь."""

        self._converter_path()
        self._dependencies()
        for font in self._font_files:
            if not font.is_file():
                raise FileNotFoundError(f"Не найден шрифт для текстового слоя: {font}")

    def convert(
        self,
        source: Path,
        destination: Path,
        *,
        include_model_frames: bool,
        include_layouts: bool,
    ) -> HeadlessResult:
        """Создаёт один многостраничный PDF и возвращает описание листов."""

        if not source.is_file():
            raise FileNotFoundError(f"Не найден DWG: {source}")
        if source.suffix.casefold() != ".dwg":
            raise ValueError(f"Ожидался DWG-файл, получено: {source.name}")

        ezdxf, pymupdf, drawing = self._dependencies()
        with tempfile.TemporaryDirectory(prefix="dwg_pdf_") as temporary:
            dxf_path = self._convert_to_dxf(source, Path(temporary))
            document = ezdxf.readfile(dxf_path)
            sheets = self._detect_sheets(document, include_model_frames, include_layouts, ezdxf)
            if not sheets:
                raise RuntimeError(
                    "Не найдены листы: в DWG нет Layout и не распознаны рамки ModelSpace"
                )

            search_items = self._collect_search_items(document, drawing)
            rendered_sheets: list[_Sheet] = []
            pdf_document = pymupdf.open()
            try:
                for sheet in sheets:
                    try:
                        page_bytes = self._render_sheet(document, sheet, drawing)
                    except ValueError as error:
                        # Некоторые DWG содержат служебный Layout с пустым
                        # viewport. Его нельзя превратить в осмысленную страницу.
                        self._log(f"Пропущен пустой лист {sheet.title}: {error}")
                        continue
                    rendered_page = pymupdf.open(stream=page_bytes, filetype="pdf")
                    try:
                        self._append_search_layer(rendered_page[0], search_items, sheet)
                        # PyMuPDF может переиспользовать имя встроенного шрифта
                        # при копировании изменённой страницы прямо в общий
                        # документ. Сначала сериализуем страницу и открываем её
                        # заново: это сохраняет видимый GOST-шрифт и скрытый
                        # Unicode-слой разными ресурсами PDF.
                        prepared_bytes = rendered_page.tobytes(garbage=4, deflate=True)
                        prepared_page = pymupdf.open(stream=prepared_bytes, filetype="pdf")
                        try:
                            pdf_document.insert_pdf(prepared_page)
                        finally:
                            prepared_page.close()
                        rendered_sheets.append(sheet)
                    finally:
                        rendered_page.close()

                if not rendered_sheets:
                    raise RuntimeError("Все найденные листы оказались пустыми при рендеринге")
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary_pdf = destination.with_suffix(".partial.pdf")
                pdf_document.save(temporary_pdf, garbage=4, deflate=True)
                temporary_pdf.replace(destination)
            finally:
                pdf_document.close()

        return HeadlessResult(
            output_pdf=destination,
            sheet_count=len(rendered_sheets),
            formats=tuple(sheet.format_name for sheet in rendered_sheets),
        )

    def _converter_path(self) -> Path:
        """Находит ODA File Converter без привязки к ОС или nanoCAD."""

        candidates: list[Path] = []
        if self._requested_converter:
            candidates.append(self._requested_converter.expanduser())
        environment_value = os.environ.get("ODA_FILE_CONVERTER")
        if environment_value:
            candidates.append(Path(environment_value))
        for executable in ("ODAFileConverter", "ODAFileConverter.exe"):
            discovered = shutil.which(executable)
            if discovered:
                candidates.append(Path(discovered))

        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        raise FileNotFoundError(
            "Не найден ODA File Converter. Укажите ODAFileConverter(.exe) в настройках "
            "или задайте переменную ODA_FILE_CONVERTER. nanoCAD для работы не нужен."
        )

    def _convert_to_dxf(self, source: Path, temporary_dir: Path) -> Path:
        """Запускает ODA только для одного файла и находит созданный DXF."""

        command = [
            str(self._converter_path()),
            str(source.parent),
            str(temporary_dir),
            "ACAD2013",
            "DXF",
            "0",
            "1",
            source.name,
        ]
        self._log(f"ODA File Converter: {source.name}")
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self._timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout or "нет диагностического сообщения").strip()
            raise RuntimeError(f"ODA File Converter завершился с кодом {completed.returncode}: {details}")

        expected_name = source.with_suffix(".dxf").name.casefold()
        dxf_files = [path for path in temporary_dir.rglob("*.dxf") if path.name.casefold() == expected_name]
        if not dxf_files:
            dxf_files = list(temporary_dir.rglob("*.dxf"))
        if len(dxf_files) != 1:
            raise RuntimeError(f"ODA File Converter не создал однозначный DXF для {source.name}")
        return dxf_files[0]

    def _dependencies(self) -> tuple[Any, Any, dict[str, Any]]:
        """Откладывает импорт тяжёлых библиотек до фактического запуска."""

        try:
            import ezdxf
            import pymupdf
            from ezdxf import bbox, disassemble
            from ezdxf.addons.drawing import Frontend, RenderContext, layout
            from ezdxf.addons.drawing.config import BackgroundPolicy, Configuration
            from ezdxf.addons.drawing.properties import table_key
            from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend
            from ezdxf.fonts import fonts
            from ezdxf.math import BoundingBox2d
        except ImportError as error:
            raise RuntimeError(
                "Не установлены зависимости автономного экспорта. Выполните: "
                "pip install -r requirements.txt"
            ) from error
        return ezdxf, pymupdf, {
            "bbox": bbox,
            "disassemble": disassemble,
            "Frontend": Frontend,
            "RenderContext": RenderContext,
            "table_key": table_key,
            "fonts": fonts,
            "layout": layout,
            "Configuration": Configuration,
            "BackgroundPolicy": BackgroundPolicy,
            "PyMuPdfBackend": PyMuPdfBackend,
            "BoundingBox2d": BoundingBox2d,
        }

    def _detect_sheets(
        self,
        document: Any,
        include_model_frames: bool,
        include_layouts: bool,
        ezdxf: Any,
    ) -> list[_Sheet]:
        """Сначала берёт готовые Layout, затем именованные рамки ModelSpace."""

        sheets: list[_Sheet] = []
        if include_layouts:
            for layout in document.layouts:
                if layout.name == "Model":
                    continue
                # Пустой лист с одним служебным VIEWPORT встречается в альбомах,
                # где настоящая рамка живёт в ModelSpace. Такой объект нечего
                # рисовать; попытка превратить его в PDF даёт пустой bbox.
                if len(layout) <= 1:
                    continue
                width = float(layout.dxf.paper_width)
                height = float(layout.dxf.paper_height)
                if width > 0 and height > 0:
                    sheets.append(
                        _Sheet(
                            title=layout.name,
                            width_mm=width,
                            height_mm=height,
                            format_name=self.page_format(width, height),
                            layout=layout,
                        )
                    )

        if include_model_frames:
            sheets.extend(self._model_frame_sheets(document, ezdxf, self._layout_format_hint(document)))
        return sheets

    def _model_frame_sheets(
        self,
        document: Any,
        ezdxf: Any,
        fallback_format: str | None,
    ) -> list[_Sheet]:
        """Ищет рамки вида ``... ЛИСТ ... A2 ...`` без догадок по масштабу."""

        frames: list[tuple[float, float, _Sheet]] = []
        seen: set[tuple[int, int, int, int]] = set()
        for insert in document.modelspace().query("INSERT"):
            name = str(insert.dxf.name).upper()
            match = self._SHEET_BLOCK.search(name)
            if not any(marker in name for marker in ("ЛИСТ", "РАМК", "SHEET", "FRAME")):
                continue
            if match:
                format_name = f"A{match.group(1)}"
            elif fallback_format in self._PAPER_SIZES:
                # Пример: блок «СОлист1_» не содержит A4, но единственный
                # Layout чертежа называется A4 и надёжно задаёт формат.
                format_name = fallback_format
            else:
                continue
            try:
                box = ezdxf.bbox.extents([insert], fast=False)
                width = float(box.extmax.x - box.extmin.x)
                height = float(box.extmax.y - box.extmin.y)
            except Exception:
                continue
            if width <= 1 or height <= 1:
                continue
            signature = tuple(round(value) for value in (box.extmin.x, box.extmin.y, box.extmax.x, box.extmax.y))
            if signature in seen:
                continue
            seen.add(signature)
            paper_width, paper_height = self._paper_size_for_frame(format_name, width, height)
            frames.append(
                (
                    -float(box.extmax.y),
                    float(box.extmin.x),
                    _Sheet(
                        title=name,
                        width_mm=paper_width,
                        height_mm=paper_height,
                        format_name=format_name,
                        frame=box,
                    ),
                )
            )
        return [sheet for _, _, sheet in sorted(frames)]

    def _layout_format_hint(self, document: Any) -> str | None:
        """Берёт единственный стандартный формат из Layout как подсказку рамке."""

        formats = {
            self.page_format(float(layout.dxf.paper_width), float(layout.dxf.paper_height))
            for layout in document.layouts
            if layout.name != "Model"
        }
        standard_formats = formats.intersection(self._PAPER_SIZES)
        return next(iter(standard_formats)) if len(standard_formats) == 1 else None

    def _paper_size_for_frame(self, format_name: str, width: float, height: float) -> tuple[float, float]:
        """Сохраняет формат A0--A4 и ориентацию рамки, игнорируя масштаб DWG."""

        portrait_width, portrait_height = self._PAPER_SIZES[format_name]
        return (
            (portrait_width, portrait_height)
            if height >= width
            else (portrait_height, portrait_width)
        )

    @classmethod
    def page_format(cls, width: float, height: float) -> str:
        """Возвращает формат по настройке бумаги; иначе честно помечает CUSTOM."""

        shorter, longer = sorted((width, height))
        for name, dimensions in cls._PAPER_SIZES.items():
            expected_shorter, expected_longer = sorted(dimensions)
            if abs(shorter - expected_shorter) <= 3 and abs(longer - expected_longer) <= 3:
                return name
        return f"CUSTOM {width:g}×{height:g} мм"

    def _render_sheet(self, document: Any, sheet: _Sheet, drawing: dict[str, Any]) -> bytes:
        """Рисует лист в векторный PDF. Растровый предпросмотр не используется."""

        backend = drawing["PyMuPdfBackend"]()
        context = self._render_context(document, drawing)
        frontend = drawing["Frontend"](
            context,
            backend,
            # PDF должен быть листом с белой бумагой, а не снимком тёмного
            # ModelSpace-фона CAD.
            config=drawing["Configuration"](
                background_policy=drawing["BackgroundPolicy"].WHITE
            ),
        )
        page = drawing["layout"].Page(sheet.width_mm, sheet.height_mm)
        settings = drawing["layout"].Settings()
        if sheet.layout is not None:
            frontend.draw_layout(sheet.layout, finalize=True)
            return backend.get_pdf_bytes(page, settings=settings)

        box = sheet.frame
        render_box = drawing["BoundingBox2d"](
            [
                (float(box.extmin.x), float(box.extmin.y)),
                (float(box.extmax.x), float(box.extmax.y)),
            ]
        )
        # Рамка может быть лишь одним из десятков листов в общей ModelSpace.
        # Отсеиваем сущности вне её до построения векторных путей: это заметно
        # ускоряет альбомы и не меняет содержимое выбранного листа.
        def intersects_sheet(entity: Any) -> bool:
            try:
                entity_box = drawing["bbox"].extents([entity], fast=True)
                if not entity_box.has_data:
                    return True
                return not (
                    entity_box.extmax.x < box.extmin.x
                    or entity_box.extmin.x > box.extmax.x
                    or entity_box.extmax.y < box.extmin.y
                    or entity_box.extmin.y > box.extmax.y
                )
            except Exception:
                # Неизвестный proxy-объект не отбрасывается только потому, что
                # его границы не удалось вычислить.
                return True

        frontend.draw_layout(
            document.modelspace(),
            finalize=True,
            filter_func=intersects_sheet,
        )
        return backend.get_pdf_bytes(page, settings=settings, render_box=render_box)

    def _render_context(self, document: Any, drawing: dict[str, Any]) -> Any:
        """Создаёт контекст и заменяет отсутствующие CAD-шрифты доступным TTF/SHX.

        В части DWG путь к шрифту в таблице TEXTSTYLE пустой, хотя сам стиль
        называется «ГОСТ 2.304» или «КРУС». Без явной подстановки ezdxf берёт
        системный шрифт, отчего в PDF появляются квадраты. Приоритет отдаётся
        файлу с похожим именем, иначе первому выбранному шрифту (в комплекте
        это GOST2304A.ttf).
        """

        if not self._font_files:
            return drawing["RenderContext"](document)

        font_manager = drawing["fonts"].font_manager
        # Важно регистрировать TTF/SHX *до* создания RenderContext. Контекст
        # кеширует обработчики шрифтов для TEXTSTYLE при инициализации; если
        # заменить FontFace позднее, часть кириллицы уже оказывается связана
        # со стандартным шрифтом и в PDF превращается в квадраты.
        for folder in {font.parent for font in self._font_files}:
            font_manager.scan_folder(folder)

        context = drawing["RenderContext"](document)

        faces: dict[str, Any] = {}
        for font in self._font_files:
            try:
                faces[self._font_key(font.stem)] = font_manager.get_font_face(font.name)
            except Exception:
                continue
        if not faces:
            return context

        fallback = next(iter(faces.values()))
        for style in document.styles:
            selected = self._font_for_style(style.dxf.name, faces, fallback)
            context.fonts[drawing["table_key"](style.dxf.name)] = selected
        return context

    @staticmethod
    def _font_key(value: str) -> str:
        """Нормализует кириллицу, цифры и разделители для сопоставления имён."""

        return "".join(character for character in value.casefold() if character.isalnum())

    def _font_for_style(self, style_name: str, faces: dict[str, Any], fallback: Any) -> Any:
        """Выбирает явно добавленный шрифт с самым близким именем стиля."""

        key = self._font_key(style_name)
        candidates = [
            (len(font_key), face)
            for font_key, face in faces.items()
            if font_key and (font_key in key or key in font_key)
        ]
        return max(candidates, default=(0, fallback), key=lambda item: item[0])[1]

    def _collect_search_items(self, document: Any, drawing: dict[str, Any]) -> list[_SearchText]:
        """Извлекает текст, в том числе из вставленных блоков, с координатами.

        PyMuPDF-рендерер превращает часть CAD-текста в векторные кривые. Здесь
        создаётся невидимый Unicode-слой в *тех же местах*, а не единый текст
        в углу страницы: тогда работают и Ctrl+F, и выделение надписи мышью.
        """

        items: list[_SearchText] = []
        character_count = 0

        def collect(entities: Any, scope: str) -> None:
            nonlocal character_count
            try:
                source = drawing["disassemble"].recursive_decompose(entities)
            except Exception:
                source = entities
            for entity in source:
                if character_count >= 500_000:
                    return
                try:
                    kind = entity.dxftype()
                    if kind in {"TEXT", "ATTRIB", "ATTDEF"}:
                        value = str(entity.dxf.text)
                    elif kind == "MTEXT":
                        value = str(entity.plain_text())
                    else:
                        continue
                    if not entity.dxf.hasattr("insert"):
                        continue
                    insert = entity.dxf.insert
                    height = float(entity.dxf.height if entity.dxf.hasattr("height") else entity.dxf.char_height)
                except Exception:
                    continue
                value = value.replace("\\P", "\n").replace("\x00", "").strip()
                if not value or height <= 0:
                    continue
                items.append(
                    _SearchText(
                        value=value,
                        x=float(insert.x),
                        y=float(insert.y),
                        height=height,
                        scope=scope,
                    )
                )
                character_count += len(value)

        collect(document.modelspace(), "Model")
        for layout in document.layouts:
            if layout.name != "Model":
                collect(layout, layout.name)
        return items

    def _append_search_layer(self, page: Any, items: list[_SearchText], sheet: _Sheet) -> None:
        """Добавляет скрытые Unicode-глифы непосредственно у CAD-надписей."""

        font = self._font_files[0] if self._font_files else self._fallback_font()
        if font is None:
            self._log("Не найден TTF для поискового слоя: PDF останется векторным, но поиск не гарантирован")
            return
        scoped_items = [item for item in items if item.scope == (sheet.layout.name if sheet.layout else "Model")]
        if not scoped_items:
            return

        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        if sheet.frame is not None:
            min_x = float(sheet.frame.extmin.x)
            min_y = float(sheet.frame.extmin.y)
            drawing_width = float(sheet.frame.extmax.x - sheet.frame.extmin.x)
            drawing_height = float(sheet.frame.extmax.y - sheet.frame.extmin.y)
        else:
            # В PaperSpace координаты листа соответствуют его физическому
            # размеру. Это даёт корректные точки вставки и для Layout.
            min_x = min_y = 0.0
            drawing_width = sheet.width_mm
            drawing_height = sheet.height_mm
        if drawing_width <= 0 or drawing_height <= 0:
            return

        for item in scoped_items:
            if not (min_x <= item.x <= min_x + drawing_width and min_y <= item.y <= min_y + drawing_height):
                continue
            x = (item.x - min_x) / drawing_width * page_width
            y = page_height - (item.y - min_y) / drawing_height * page_height
            font_size = max(0.5, min(96.0, item.height / drawing_height * page_height))
            # insert_text не меняет внешнее содержимое: режим 3 создаёт PDF
            # текст без заливки и обводки, но оставляет его выделяемым.
            for line_index, line in enumerate(item.value.splitlines() or [item.value]):
                if line:
                    page.insert_text(
                        (x, y + line_index * font_size * 1.2),
                        line,
                        fontsize=font_size,
                        fontname="dwg_search",
                        fontfile=str(font),
                        render_mode=3,
                        overlay=True,
                    )

    @staticmethod
    def _fallback_font() -> Path | None:
        """Даёт поиск кириллицы на типовых Linux/Windows установках."""

        candidates = (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path(r"C:\\Windows\\Fonts\\arial.ttf"),
        )
        return next((font for font in candidates if font.is_file()), None)
