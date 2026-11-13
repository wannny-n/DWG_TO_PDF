"""Versioned XML input for unattended DWG printing."""
from argparse import Namespace
from pathlib import Path
from xml.etree import ElementTree as ET
import re


class _NoDoctype(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ValueError('DTD не поддерживается в XML-задании')


def read_xml_job(filename: Path) -> Namespace:
    filename = filename.expanduser().resolve()
    with filename.open('rb') as stream:
        data = stream.read(1_048_577)
    if len(data) > 1_048_576:
        raise ValueError('XML-задание превышает 1 МиБ')
    root = ET.fromstring(data, parser=ET.XMLParser(target=_NoDoctype()))
    if root.tag != 'DwgToPdf' or root.attrib != {'version': '1'}:
        raise ValueError('Ожидается корневой тег <DwgToPdf version="1">')

    def scalar(node, attributes=()):
        if len(node) or set(node.attrib) - set(attributes) or not (node.text or '').strip():
            raise ValueError(f'Некорректное значение <{node.tag}>')
        return node.text.strip()

    def path(value, base=filename.parent):
        return (base / Path(value).expanduser()).resolve()

    def flag(name, default):
        if name not in nodes:
            return default
        value = scalar(nodes[name]).lower()
        if value not in ('true', 'false', '1', '0'):
            raise ValueError(f'<{name}>: ожидается true или false')
        return value in ('true', '1')

    nodes = {}
    allowed = {'Sources','OutputDir','Merge','CombinedPdf','IncludeModelFrames',
               'IncludeLayouts','Oda','Fonts','Overwrite','SortOrder'}
    for node in root:
        if node.tag not in allowed or node.tag in nodes:
            raise ValueError(f'Неизвестный или повторный тег <{node.tag}>')
        nodes[node.tag] = node
    if not {'Sources', 'OutputDir'} <= nodes.keys():
        raise ValueError('Требуются теги <Sources> и <OutputDir>')
    sources, selected, excluded = [], {}, {}
    source_group = nodes['Sources']
    if source_group.attrib or not len(source_group):
        raise ValueError('<Sources> должен содержать <File> или <Folder>')
    for node in source_group:
        if node.tag not in ('File', 'Folder'):
            raise ValueError(f'<Sources>: неизвестный тег <{node.tag}>')
        source = path(scalar(node, ('sheets', 'excludeSheets') if node.tag == 'File' else ()))
        if node.tag == 'Folder' and not source.is_dir():
            raise ValueError(f'Папка не найдена: {source}')
        if node.tag == 'File' and (not source.is_file() or source.suffix.lower() != '.dwg'):
            raise ValueError(f'DWG-файл не найден: {source}')
        sources.append(source)
        if 'sheets' in node.attrib and 'excludeSheets' in node.attrib:
            raise ValueError(f'Задайте либо sheets, либо excludeSheets: {source}')
        for attribute, selections in (('sheets', selected), ('excludeSheets', excluded)):
            if attribute not in node.attrib:
                continue
            value = node.attrib[attribute].strip()
            if not re.fullmatch(r'[1-9][0-9]*(\s*,\s*[1-9][0-9]*)*', value):
                raise ValueError(f'{attribute}: укажите номера листов через запятую, начиная с 1')
            numbers = tuple(int(n) for n in value.split(','))
            if len(numbers) != len(set(numbers)) or str(source) in selected or str(source) in excluded:
                raise ValueError(f'Повторный выбор листов: {source}')
            selections[str(source)] = numbers

    order = scalar(nodes['SortOrder']).lower() if 'SortOrder' in nodes else 'manual'
    if order not in ('manual', 'natural', 'alphabetical'):
        raise ValueError('<SortOrder>: ожидается manual, natural или alphabetical')

    output = path(scalar(nodes['OutputDir']))
    merge = flag('Merge', True)
    combined = None
    if 'CombinedPdf' in nodes:
        combined = path(scalar(nodes['CombinedPdf']), output)
        if not merge or combined.suffix.lower() != '.pdf':
            raise ValueError('<CombinedPdf> требует <Merge>true</Merge> и расширение .pdf')
    model, layouts = flag('IncludeModelFrames', True), flag('IncludeLayouts', True)
    if not (model or layouts):
        raise ValueError('Включите печать рамок модели или листов Layout')
    fonts = []
    if 'Fonts' in nodes:
        if nodes['Fonts'].attrib:
            raise ValueError('<Fonts> не принимает атрибуты')
        for node in nodes['Fonts']:
            if node.tag != 'Font':
                raise ValueError(f'<Fonts>: неизвестный тег <{node.tag}>')
            font = path(scalar(node))
            if not font.is_file() or font.suffix.lower() not in ('.ttf', '.otf', '.shx'):
                raise ValueError(f'Не найден шрифт TTF/OTF/SHX: {font}')
            fonts.append(font)
    oda = path(scalar(nodes['Oda'])) if 'Oda' in nodes else None
    if oda is not None and not oda.is_file():
        raise ValueError(f'Конвертер ODA не найден: {oda}')
    return Namespace(sources=sources, output=output, oda=oda, font=fonts,
                     no_model_frames=not model, no_layouts=not layouts,
                     merge=merge, combined_pdf=combined, overwrite=flag('Overwrite', False),
                     native_exporter=None, use_native_exporter=False,
                     selected_sheets=selected, excluded_sheets=excluded,
                     sort_order=order, xml_job=True)
