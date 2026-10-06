"""Автономный конвейер DWG -> векторный PDF.

Модуль работает автономно. ODA File
Converter читает DWG в отдельном процессе и создаёт временный DXF, а ezdxf и
PyMuPDF строят PDF. Исходный DWG при этом не изменяется.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable, Iterable
from .paper_formats import PAPER_SIZES, format_from_name, page_format


Log = Callable[[str], None]
Progress = Callable[[float, str], None]


@dataclass(frozen=True)
class HeadlessResult:
    """Результат обработки одного исходного файла."""

    output_pdf: Path
    sheet_count: int
    formats: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Sheet:
    """Один лист, который нужно добавить в итоговый PDF."""

    title: str
    width_mm: float
    height_mm: float
    format_name: str
    layout: Any | None = None
    frame: Any | None = None
    rotation: int = 0


@dataclass(frozen=True)
class _SearchText:
    """Текст из DWG и его положение в координатах исходного листа."""

    value: str
    x: float
    y: float
    height: float
    scope: str
    layer: str = "0"


class HeadlessDwgConverter:
    """Преобразует один DWG автономно.

    Для чтения закрытого формата DWG используется установленный пользователем
    ODA File Converter. Это отдельная утилита без CAD-интерфейса. Рендеринг PDF
    выполняется локально, поэтому обработка не зависит от принтера Windows.
    """

    _PAPER_SIZES = PAPER_SIZES

    def __init__(
        self,
        converter_exe: Path | None,
        font_files: Iterable[Path],
        log: Log,
        timeout_seconds: int = 600,
        progress: Progress | None = None,
    ) -> None:
        self._requested_converter = converter_exe
        self._font_files = tuple(Path(path) for path in font_files)
        self._log = log
        self._timeout_seconds = timeout_seconds
        self._warnings: list[str] = []
        self._page_transforms: dict[int, Any] = {}
        self._bbox_cache: Any = None
        self._context: Any = None
        self._fonts_registered = False
        self._progress = progress
        self._progress_value = 0.0
        self._progress_stage = ""
        self._progress_time = 0.0
        self._render_range = (25.0, 40.0)
        self._text_range = (65.0, 15.0)
        self._sheet_label = ""

    def _report(self, percent: float, stage: str) -> None:
        if self._progress is None:
            return
        percent = max(self._progress_value, min(100.0, percent))
        now = time.monotonic()
        if stage == self._progress_stage and now - self._progress_time < 0.15 and percent < 100:
            return
        self._progress_value = percent
        self._progress_stage = stage
        self._progress_time = now
        self._progress(percent, stage)

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
        self._progress_value = 0.0
        self._progress_stage = ""
        self._report(0, "Подготовка")

        ezdxf, pymupdf, drawing = self._dependencies()
        self._warnings: list[str] = []
        self._page_transforms.clear()
        self._bbox_cache = drawing["bbox"].Cache()
        self._context = None
        with tempfile.TemporaryDirectory(prefix="dwg_pdf_") as temporary:
            self._report(2, "Чтение DWG через ODA")
            dxf_path = self._convert_to_dxf(source, Path(temporary))
            self._report(8, "Загрузка чертежа")
            document = ezdxf.readfile(dxf_path)
            self._report(15, "Поиск листов и рамок")
            sheets = self._detect_sheets(document, include_model_frames, include_layouts, ezdxf)
            if not sheets:
                raise RuntimeError(
                    "Не найдены листы: в DWG нет Layout и не распознаны рамки ModelSpace"
                )

            self._report(18, "Подготовка текста")
            search_items = self._collect_search_items(document, drawing)
            self._report(25, f"Найдено листов: {len(sheets)}")
            rendered_sheets: list[_Sheet] = []
            pdf_document = pymupdf.open()
            try:
                for index, sheet in enumerate(sheets):
                    portion = 68.0 / len(sheets)
                    base = 25 + portion * index
                    self._sheet_label = f"Лист {index + 1}/{len(sheets)}"
                    self._render_range = (base, portion * 0.7)
                    self._text_range = (base + portion * 0.8, portion * 0.18)
                    self._report(base, f"{self._sheet_label} — графика")
                    try:
                        page_bytes = self._render_sheet(document, sheet, drawing)
                    except ValueError as error:
                        # Некоторые DWG содержат служебный Layout с пустым
                        # viewport. Его нельзя превратить в осмысленную страницу.
                        self._log(f"Пропущен пустой лист {sheet.title}: {error}")
                        continue
                    rendered_page = pymupdf.open(stream=page_bytes, filetype="pdf")
                    try:
                        self._report(base + portion * 0.8, f"{self._sheet_label} — поисковый текст")
                        page_items = self._sheet_search_items(search_items, sheet, document, drawing)
                        self._append_search_layer(rendered_page[0], page_items, sheet)
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
                        self._report(base + portion, f"{self._sheet_label} — готов")
                    finally:
                        rendered_page.close()

                if not rendered_sheets:
                    raise RuntimeError("Все найденные листы оказались пустыми при рендеринге")
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._report(94, "Сохранение PDF")
                temporary_pdf = destination.with_suffix(".partial.pdf")
                pdf_document.save(temporary_pdf, garbage=4, deflate=True)
                temporary_pdf.replace(destination)
            finally:
                pdf_document.close()

        self._report(100, "PDF создан")
        return HeadlessResult(
            output_pdf=destination,
            sheet_count=len(rendered_sheets),
            formats=tuple(sheet.format_name for sheet in rendered_sheets),
            warnings=tuple(self._warnings),
        )

    def _converter_path(self) -> Path:
        """Находит ODA File Converter на поддерживаемой ОС."""

        from .oda_setup import find_installed_converter

        if self._requested_converter:
            requested = self._requested_converter.expanduser()
            if not requested.is_file():
                raise FileNotFoundError(f"Не найден указанный ODA File Converter: {requested}")
            return requested.resolve()
        discovered_converter = find_installed_converter()
        if discovered_converter:
            return discovered_converter
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
            "или задайте переменную ODA_FILE_CONVERTER."
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
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
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
            from ezdxf.path import make_path
            from ezdxf.tools.clipping_portal import find_best_clipping_shape
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
            "make_path": make_path,
            "find_best_clipping_shape": find_best_clipping_shape,
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
            for name in document.layouts.names_in_taborder():
                layout = document.layouts.get(name)
                if layout.name == "Model":
                    continue
                # Пустой лист с одним служебным VIEWPORT встречается в альбомах,
                # где настоящая рамка живёт в ModelSpace. Такой объект нечего
                # рисовать; попытка превратить его в PDF даёт пустой bbox.
                if not any(entity.dxftype() != "VIEWPORT" or self._printable_viewport(entity) for entity in layout):
                    continue
                width = float(layout.dxf.paper_width)
                height = float(layout.dxf.paper_height)
                if width > 0 and height > 0:
                    # DXF stores paper dimensions in mm even for inch layouts.
                    # PaperSpace coordinates include the plot scale and origin.
                    units = 25.4 if layout.dxf.plot_paper_units == 0 else 1.0
                    scale = units * float(layout.dxf.scale_numerator) / (float(layout.dxf.scale_denominator) or 1.0)
                    if scale <= 0:
                        scale = units
                    min_x = -(float(layout.dxf.left_margin) + float(layout.dxf.plot_origin_x_offset)) / scale
                    min_y = -(float(layout.dxf.bottom_margin) + float(layout.dxf.plot_origin_y_offset)) / scale
                    from ezdxf.math import BoundingBox2d
                    paper_box = BoundingBox2d([(min_x, min_y), (min_x + width / scale, min_y + height / scale)])
                    rotation = int(layout.dxf.plot_rotation) % 4 * 90
                    if rotation in (90, 270):
                        width, height = height, width
                    sheets.append(
                        _Sheet(
                            title=layout.name,
                            width_mm=width,
                            height_mm=height,
                            format_name=self.page_format(width, height),
                            layout=layout,
                            frame=paper_box,
                            rotation=rotation,
                        )
                    )

        if include_model_frames:
            sheets.extend(self._model_frame_sheets(document, ezdxf, self._layout_format_hint(document)))
        return sheets

    @staticmethod
    def _printable_viewport(viewport: Any) -> bool:
        return (viewport.dxf.id != 1 and viewport.dxf.status > 0
                and viewport.dxf.width > 0 and viewport.dxf.height > 0
                and viewport.dxf.view_height > 0)

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
            named_format = format_from_name(name)
            if not any(marker in name for marker in ("ЛИСТ", "РАМК", "SHEET", "FRAME")):
                continue
            if named_format:
                format_name = named_format
            elif fallback_format in self._PAPER_SIZES:
                # Пример: блок «СОлист1_» не содержит A4, но единственный
                # Layout чертежа называется A4 и надёжно задаёт формат.
                format_name = fallback_format
            else:
                continue
            try:
                box = ezdxf.bbox.extents([insert], fast=False, cache=self._bbox_cache)
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
        """Сохраняет основной/удлинённый формат и ориентацию независимо от масштаба."""

        portrait_width, portrait_height = self._PAPER_SIZES[format_name]
        return (
            (portrait_width, portrait_height)
            if height >= width
            else (portrait_height, portrait_width)
        )

    @classmethod
    def page_format(cls, width: float, height: float) -> str:
        """Распознаёт основные и удлинённые форматы, сохраняет произвольные размеры."""
        return page_format(width, height)

    def _render_sheet(self, document: Any, sheet: _Sheet, drawing: dict[str, Any]) -> bytes:
        """Рисует лист в векторный PDF. Растровый предпросмотр не используется."""

        backend = drawing["PyMuPdfBackend"]()
        if self._context is None:
            self._context = self._render_context(document, drawing)
        context = self._context
        converter = self
        if sheet.layout is not None:
            viewport_count = sum(self._printable_viewport(vp) for vp in sheet.layout.query("VIEWPORT"))
            work_count = len(sheet.layout) + len(document.modelspace()) * viewport_count
        else:
            work_count = len(document.modelspace())

        class ExportFrontend(drawing["Frontend"]):
            _depth = 0
            _processed = 0

            def _counted(self, entities: Any) -> Any:
                for entity in entities:
                    yield entity
                    self._processed += 1
                    if self._processed % 64 == 0:
                        base, span = converter._render_range
                        converter._report(base + span * min(0.98, self._processed / max(1, work_count)),
                                          f"{converter._sheet_label} — графика")

            def draw_entities(self, entities: Any, *, filter_func: Any = None) -> None:
                if self._depth == 0:
                    entities = self._counted(entities)
                self._depth += 1
                try:
                    super().draw_entities(entities, filter_func=filter_func)
                finally:
                    self._depth -= 1

            def draw_entities_callback(self, ctx: Any, entities: Any) -> None:
                self._depth += 1
                try:
                    super().draw_entities_callback(ctx, self._counted(entities))
                finally:
                    self._depth -= 1

            def draw_image_entity(self, entity: Any, properties: Any) -> None:
                try:
                    super().draw_image_entity(entity, properties)
                except OSError:
                    warning = "Внешнее растровое изображение недоступно; оно пропущено в PDF"
                    if warning not in converter._warnings:
                        converter._warnings.append(warning)
                        converter._log(warning)

        frontend = ExportFrontend(
            context,
            backend,
            # PDF должен быть листом с белой бумагой, а не снимком тёмного
            # ModelSpace-фона CAD.
            config=drawing["Configuration"](
                background_policy=drawing["BackgroundPolicy"].WHITE
            ),
            bbox_cache=self._bbox_cache,
        )
        page = drawing["layout"].Page(sheet.width_mm, sheet.height_mm)
        settings = drawing["layout"].Settings(content_rotation=sheet.rotation, crop_at_margins=True)
        if sheet.layout is not None:
            # Render printable viewports explicitly: a single viewport with
            # status=1 is not necessarily the editor's main viewport (id=1).
            frontend.draw_layout(sheet.layout, finalize=False,
                                 filter_func=lambda entity: entity.dxftype() != "VIEWPORT")
            for viewport in sorted(sheet.layout.query("VIEWPORT"), key=lambda vp: vp.dxf.status):
                if self._printable_viewport(viewport):
                    frontend.draw_viewport(viewport)
            frontend.pipeline.finalize()
            self._report(sum(self._render_range), f"{self._sheet_label} — создание страницы PDF")
            render_box = sheet.frame
            self._store_page_transform(sheet, page, settings, render_box, drawing)
            return backend.get_pdf_bytes(page, settings=settings, render_box=render_box)

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
                entity_box = drawing["bbox"].extents([entity], fast=True, cache=self._bbox_cache)
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
        self._store_page_transform(sheet, page, settings, render_box, drawing)
        self._report(sum(self._render_range), f"{self._sheet_label} — создание страницы PDF")
        return backend.get_pdf_bytes(page, settings=settings, render_box=render_box)

    def _store_page_transform(self, sheet: _Sheet, page: Any, settings: Any,
                              render_box: Any, drawing: dict[str, Any]) -> None:
        # Use exactly the renderer's uniform scale, rotation and letterboxing
        # for the searchable text, including frames whose aspect ratio differs.
        from dataclasses import replace
        output_space = max(int(sheet.width_mm * 72 / 25.4), int(sheet.height_mm * 72 / 25.4))
        output_settings = replace(settings, output_coordinate_space=output_space)
        self._page_transforms[id(sheet)] = drawing["layout"].Layout(render_box, flip_y=True).get_placement_matrix(
            page, settings=output_settings, top_origin=True)

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
        if not self._fonts_registered:
            for folder in {font.parent for font in self._font_files}:
                font_manager.scan_folder(folder)
            self._fonts_registered = True

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
        processed = 0

        def collect(entities: Any, scope: str) -> None:
            nonlocal character_count, processed
            try:
                source = drawing["disassemble"].recursive_decompose(entities)
            except Exception:
                source = entities
            for entity in source:
                processed += 1
                if processed % 500 == 0:
                    self._report(18, f"Подготовка текста: {processed} объектов")
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
                        layer=str(entity.dxf.get("layer", "0")),
                    )
                )
                character_count += len(value)

        collect(document.modelspace(), "Model")
        for layout in document.layouts:
            if layout.name != "Model":
                collect(layout, layout.name)
        return items

    def _sheet_search_items(self, items: list[_SearchText], sheet: _Sheet,
                            document: Any, drawing: dict[str, Any]) -> list[_SearchText]:
        if sheet.layout is None:
            return [item for item in items if item.scope == "Model"]
        result = [item for item in items if item.scope == sheet.layout.name]
        context = self._context or drawing["RenderContext"](document)
        for viewport in sheet.layout.query("VIEWPORT"):
            if not self._printable_viewport(viewport) or not viewport.is_top_view:
                continue
            path = drawing["make_path"](viewport)
            if not len(path):
                continue
            clip = drawing["find_best_clipping_shape"](list(path.flattening(0.1)))
            matrix = viewport.get_transformation_matrix()
            viewport_context = context.from_viewport(viewport)
            for item in items:
                if item.scope != "Model":
                    continue
                layer = viewport_context.layers.get(drawing["table_key"](item.layer))
                if layer is not None and not layer.is_visible:
                    continue
                position = matrix.transform((item.x, item.y, 0))
                if clip.clip_point(position.vec2) is None:
                    continue
                result.append(_SearchText(item.value, position.x, position.y,
                                          item.height * viewport.get_scale(), sheet.layout.name, item.layer))
        return result

    def _append_search_layer(self, page: Any, items: list[_SearchText], sheet: _Sheet) -> None:
        """Добавляет скрытые Unicode-глифы непосредственно у CAD-надписей."""

        font = next((font for font in self._font_files if font.suffix.casefold() in {".ttf", ".otf"}), self._fallback_font())
        if font is None:
            self._log("Не найден TTF для поискового слоя: PDF останется векторным, но поиск не гарантирован")
            return
        if not items:
            return

        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        matrix = self._page_transforms.get(id(sheet))
        if matrix is None:
            return
        # A page.insert_text() call commits a new content stream each time.
        # Thousands of streams make insertion/merging grow quadratically.
        # Accumulate text in a Shape and commit once for the whole page.
        shape = page.new_shape()
        for index, item in enumerate(items):
            if index % 128 == 0:
                base, span = self._text_range
                self._report(base + span * index / len(items), f"{self._sheet_label} — поисковый текст")
            position = matrix.transform((item.x, item.y, 0))
            x, y = position.x, position.y
            if not (0 <= x <= page_width and 0 <= y <= page_height):
                continue
            font_size = max(0.5, min(96.0, matrix.transform_direction((0, item.height, 0)).magnitude))
            # insert_text не меняет внешнее содержимое: режим 3 создаёт PDF
            # текст без заливки и обводки, но оставляет его выделяемым.
            for line_index, line in enumerate(item.value.splitlines() or [item.value]):
                if line:
                    shape.insert_text(
                        (x, y + line_index * font_size * 1.2),
                        line,
                        fontsize=font_size,
                        fontname="dwg_search",
                        fontfile=str(font),
                        render_mode=3,
                        rotate=sheet.rotation,
                    )
        shape.commit(overlay=True)

    @staticmethod
    def _fallback_font() -> Path | None:
        """Даёт поиск кириллицы на типовых Linux/Windows установках."""

        candidates = (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path(r"C:\\Windows\\Fonts\\arial.ttf"),
        )
        return next((font for font in candidates if font.is_file()), None)
