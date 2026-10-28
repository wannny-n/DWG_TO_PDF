"""Встроенные OLE-изображения и восстановление геометрии proxy SHELL."""
from __future__ import annotations

from io import BytesIO
import struct
from types import MethodType
from typing import Any, Callable
from functools import lru_cache


@lru_cache(maxsize=8)
def ole_image(data: bytes) -> Any:
    """Читает сохранённое представление OLE, не запуская встроенный документ."""
    import olefile
    from PIL import Image

    payloads = [data]
    offset = data.find(olefile.MAGIC, 0, 4096)
    if offset >= 0:
        with olefile.OleFileIO(BytesIO(data[offset:])) as storage:
            payloads = [storage.openstream(name).read() for name in storage.listdir()
                        if name[-1] == 'CONTENTS' or name[-1].startswith('\x02OlePres')]
    for payload in payloads:
        try:
            image = Image.open(BytesIO(payload))
            if image.format in ('WMF', 'EMF'):
                # Windows Pillow renders the complete saved metafile with GDI.
                if image.width * image.height * (300 / 72) ** 2 > 40_000_000:
                    raise ValueError('Слишком большое OLE-изображение')
                image.load(dpi=300)
            else:
                image.load()
            return image.convert('RGBA')
        except (OSError, ValueError, NotImplementedError):
            pass
        # Static metafiles often contain a DIB bitmap. This fallback also
        # works on systems where Pillow cannot render Windows metafiles.
        start = 0
        while True:
            index = payload.find(b'\x28\x00\x00\x00', start)
            if index < 0:
                break
            start = index + 4
            if index + 40 > len(payload):
                continue
            _, width, height, planes, bits, compression, _, _, _, colors, _ = struct.unpack_from('<IiiHHIIiiII', payload, index)
            if not (planes == 1 and bits in (1, 4, 8, 16, 24, 32) and compression in (0, 3)
                    and width > 0 and height != 0 and width * abs(height) <= 40_000_000):
                continue
            palette = (colors or (1 << bits)) * 4 if bits <= 8 else 0
            pixel_offset = 40 + palette + (12 if compression == 3 else 0)
            length = pixel_offset + ((width * bits + 31) // 32 * 4) * abs(height)
            if index + length > len(payload):
                continue
            header = struct.pack('<2sIHHI', b'BM', length + 14, 0, 0, pixel_offset + 14)
            try:
                image = Image.open(BytesIO(header + payload[index:index + length]))
                image.load()
                return image.convert('RGBA')
            except (OSError, ValueError):
                continue
    raise ValueError('Нет поддерживаемого сохранённого изображения OLE')


def prepare_proxy_entities(document: Any, warn: Callable[[str], None]) -> None:
    """Кеширует proxy-примитивы для bbox, рендера и поиска текста.

    В отдельных SHELL сохранены вершины и грани, но отсутствует конец
    необязательных атрибутов нормалей. Сохраняем геометрию и продолжаем
    читать следующие записи proxy, включая текст.
    """
    from ezdxf.proxygraphic import ProxyGraphic, ProxyGraphicError
    from ezdxf.tools.binarydata import ByteStream, EndOfBufferError
    from ezdxf.lldxf import const
    from ezdxf.math import Vec3

    # ezdxf intentionally treats OLE2FRAME as non-copyable. INSERT expansion
    # therefore drops embedded logos. Supply a drawing-only copy/transform
    # adapter; the stored OLE payload remains unchanged and is never executed.
    from ezdxf.entities import factory
    from ezdxf.math import Matrix44, BoundingBox
    from ezdxf.lldxf.types import DXFVertex
    import copy

    def bind_ole(entity):
        entity.copy = MethodType(copy_ole,entity)
        entity.transform = MethodType(transform_ole,entity)

    def copy_ole(entity,copy_strategy=None):
        result = factory.new('OLE2FRAME',dxfattribs={k:v for k,v in entity.dxf.all_existing_dxf_attribs().items()
                                                  if k not in ('handle','owner')})
        result.doc = entity.doc
        result.acdb_ole2frame = copy.deepcopy(entity.acdb_ole2frame)
        result._pdf_ole_box = entity._pdf_ole_box
        result._pdf_ole_matrix = entity._pdf_ole_matrix.copy()
        result.set_source_of_copy(entity)
        bind_ole(result)
        return result

    def transform_ole(entity,matrix):
        entity._pdf_ole_matrix @= matrix
        box=entity._pdf_ole_box
        bounds=BoundingBox(entity._pdf_ole_matrix.transform_vertices(box.rect_vertices()))
        entity.acdb_ole2frame = type(entity.acdb_ole2frame)(
            DXFVertex(tag.code,bounds.extmin if tag.code==10 else bounds.extmax)
            if tag.code in (10,11) else tag for tag in entity.acdb_ole2frame)
        return entity

    for entity in list(document.entitydb.values()):
        if entity.dxftype()=='OLE2FRAME' and entity.bbox().has_data:
            entity._pdf_ole_box = entity.bbox()
            entity._pdf_ole_matrix = Matrix44()
            bind_ole(entity)

    class CompatibleProxyGraphic(ProxyGraphic):
        repaired = False

        def shell(self, data: bytes) -> Any:
            result = self._read_shell(data)
            # A filled black SHELL directly behind its label is a CAD
            # background mask, not a wireframe mesh. Keep the fill trait until
            # we can check it against the following text in WCS.
            result._pdf_background_shell = self.fill and self.true_color == 0
            return result

        def _read_shell(self, data: bytes) -> Any:
            try:
                return super().shell(data)
            except EndOfBufferError:
                stream = ByteStream(data)
                count = stream.read_long()
                if count > len(data) // 24:
                    raise
                vertices = [Vec3(stream.read_vertex()) for _ in range(count)]
                entries = stream.read_long()
                faces = []
                consumed = 0
                while consumed < entries:
                    count = abs(stream.read_signed_long())
                    if count < 3 or count > entries - consumed - 1:
                        raise ProxyGraphicError('Повреждены грани proxy SHELL')
                    indices = [stream.read_long() for _ in range(count)]
                    if any(index >= len(vertices) for index in indices):
                        raise ProxyGraphicError('Повреждены индексы proxy SHELL')
                    faces.append([vertices[index] for index in indices])
                    consumed += count + 1
                attributes = self._build_dxf_attribs()
                attributes['flags'] = const.POLYLINE_POLYFACE
                polyface = self._factory('POLYLINE', dxfattribs=attributes)
                polyface.append_faces(faces)
                polyface.optimize()
                self.repaired = True
                return polyface

    def cached_entities(entity: Any) -> Any:
        # INSERT.virtual_entities() transforms returned entities in place.
        # Never expose the cached originals to bbox, nested INSERTs or rendering.
        return (primitive.copy() for primitive in entity._pdf_proxy_entities)

    for entity in list(document.entitydb.values()):
        if entity.dxftype() != 'ACAD_PROXY_ENTITY' or not entity.proxy_graphic:
            continue
        parser = CompatibleProxyGraphic(entity.proxy_graphic, document)
        primitives = []
        try:
            for primitive in parser.virtual_entities():
                primitive.set_source_of_copy(entity)
                primitives.append(primitive)
        except ProxyGraphicError as error:
            warn(f'Proxy-объект {entity.dxf.handle}: сохранена доступная часть графики; {error}')
        if parser.repaired:
            warn(f'Proxy-объект {entity.dxf.handle}: геометрия восстановлена; повреждённые атрибуты нормалей пропущены')
        entity._pdf_proxy_entities = restore_proxy_text_masks(primitives)
        entity.__virtual_entities__ = MethodType(cached_entities, entity)


def restore_proxy_text_masks(primitives: list[Any]) -> list[Any]:
    """Restore small filled rectangular backgrounds before proxy TEXT labels.

    Preserve ordinary polyfaces, table borders and black solids. Only a filled
    background-colour quad immediately enclosing a label becomes a WIPEOUT,
    which follows the paper background and has no printable boundary.
    """
    from ezdxf.entities import factory
    from ezdxf.math import BoundingBox2d, Vec2

    result = list(primitives)
    for index, (shell, text) in enumerate(zip(primitives, primitives[1:])):
        if not getattr(shell, '_pdf_background_shell', False) or text.dxftype() != 'TEXT':
            continue
        faces = list(shell.faces())
        if len(faces) != 1:
            continue
        vertices = [v.dxf.location for v in faces[0][:-1]]
        box = BoundingBox2d(vertices)
        h = float(text.dxf.height)
        if h <= 0 or not box.has_data or min(box.size) <= 0:
            continue
        if not all(min(abs(v.x-box.extmin.x), abs(v.x-box.extmax.x)) < 1e-6 and
                   min(abs(v.y-box.extmin.y), abs(v.y-box.extmax.y)) < 1e-6 for v in vertices):
            continue
        if (not box.inside(Vec2(text.dxf.insert)) or
                not .8*h <= box.size.y <= 2*h or box.size.x > h*max(4, 2*len(text.dxf.text))):
            continue
        mask = factory.new('WIPEOUT')
        mask.set_masking_area(vertices)
        mask.dxf.layer = shell.dxf.layer
        result[index] = mask
    return result
