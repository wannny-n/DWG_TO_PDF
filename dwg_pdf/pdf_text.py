"""Текст PDF из тех же матриц, которыми CAD-рендерер размещает надписи."""
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class TextRun:
    text: str
    matrix: Any
    font: Path
    fontsize: float
    color: tuple[float, float, float]
    visible: bool = True


def capture_text(pipeline: Any, sheet: Any, runs: list[TextRun], fallback_font: Path | None = None) -> None:
    from ezdxf.addons.drawing.pipeline import prepare_string_for_rendering
    from ezdxf.fonts import fonts
    from ezdxf.math import Matrix44, Vec2

    original = pipeline.draw_text
    cache = {}

    def draw(text, transform, properties, cap_height, dxftype='TEXT'):
        if not text.strip():
            return
        face = properties.font or pipeline.default_font_face
        try:
            key = face.filename
            if key not in cache:
                abstract = pipeline.text_engine.get_font(face)
                glyphs = abstract.glyph_cache
                font = glyphs.font
                path = Path(font.reader.file.name)
                cache[key] = (abstract, path, font['head'].unitsPerEm,
                              glyphs.font_measurements)
            abstract, path, em, metrics = cache[key]
            value = prepare_string_for_rendering(text, dxftype)
            width = abstract.text_width(value) * cap_height
            corners = [(0, -abstract.measurements.descender_height * cap_height),
                       (width, -abstract.measurements.descender_height * cap_height),
                       (width, cap_height), (0, cap_height)]
            vertices = list(transform.transform_vertices(corners))
            portal = pipeline.clipping_portal
            if portal.is_active and any(portal.clip_point(Vec2(p)) is None for p in vertices):
                original(text, transform, properties, cap_height, dxftype)
                return
            matrix = portal.transform_matrix(transform.copy())
            if sheet.layout is None and any(not sheet.frame.inside(Vec2(p)) for p in matrix.transform_vertices(corners)):
                original(text, transform, properties, cap_height, dxftype)
                return
            correction = -metrics.baseline * cap_height / metrics.cap_height
            matrix = Matrix44.translate(0, correction, 0) * matrix
            color = properties.color[:7]
            rgb = tuple(int(color[i:i+2], 16) / 255 for i in (1, 3, 5))
            runs.append(TextRun(value, matrix, path, cap_height * em / metrics.cap_height, rgb))
        except (AttributeError, KeyError, OSError, ValueError, TypeError):
            original(text, transform, properties, cap_height, dxftype)
            # SHX stays as its original vector strokes. Retain searchable text
            # at the actual rendered placement, fitted to the stroke font width.
            if fallback_font is None:
                return
            try:
                value = prepare_string_for_rendering(text, dxftype)
                source_font = pipeline.text_engine.get_font(face)
                target_font = fonts.make_font(str(fallback_font),1)
                metrics = target_font.glyph_cache.font_measurements
                width = source_font.text_width(value)*cap_height
                native_width = target_font.text_width(value)*cap_height
                if native_width <= 0:
                    return
                corners = [(0,0),(width,0),(width,cap_height),(0,cap_height)]
                portal = pipeline.clipping_portal
                if portal.is_active and any(portal.clip_point(Vec2(p)) is None for p in transform.transform_vertices(corners)):
                    return
                matrix = portal.transform_matrix(transform.copy())
                if sheet.layout is None and any(not sheet.frame.inside(Vec2(p)) for p in matrix.transform_vertices(corners)):
                    return
                matrix = Matrix44.scale(width/native_width,1,1)*matrix
                em = target_font.glyph_cache.font['head'].unitsPerEm
                runs.append(TextRun(value,matrix,fallback_font,cap_height*em/metrics.cap_height,(0,0,0),False))
            except (AttributeError,KeyError,OSError,ValueError,TypeError):
                pass

    pipeline.draw_text = draw


def write_text(page: Any, runs: list[TextRun], placement: Any) -> None:
    import pymupdf

    shape = page.new_shape()
    registered = {}
    for run in runs:
        matrix = run.matrix * placement
        p = matrix.transform((0, 0, 0))
        if not page.rect.contains(pymupdf.Point(p.x, p.y)):
            continue
        x = matrix.transform_direction((1, 0, 0))
        y = matrix.transform_direction((0, 1, 0))
        if abs(x.x * y.y - x.y * y.x) < 1e-14:
            continue
        if run.font not in registered:
            name = f'cadtext{len(registered)}'
            page.insert_font(fontname=name, fontfile=str(run.font))
            registered[run.font] = name
        point = pymupdf.Point(p.x, p.y)
        # Shape's morph operates in PDF coordinates (Y up), whereas placement
        # returns page coordinates (Y down). Convert both basis vectors.
        morph = pymupdf.Matrix(x.x, -x.y, y.x, -y.y, 0, 0)
        shape.insert_text(point, run.text, fontname=registered[run.font], fontsize=run.fontsize,
                          color=run.color, morph=(point, morph), render_mode=0 if run.visible else 3)
    shape.commit()
