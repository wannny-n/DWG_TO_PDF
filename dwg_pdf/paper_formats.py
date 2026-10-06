"""Основные и дополнительные форматы ГОСТ 2.301-68, размеры в миллиметрах.

Таблица 2: https://ru.wikisource.org/wiki/ГОСТ_2.301—68
Ориентация определяется отдельно от обозначения формата.
"""

import re

PAPER_SIZES = {
    "A0": (841.0, 1189.0), "A1": (594.0, 841.0),
    "A2": (420.0, 594.0), "A3": (297.0, 420.0), "A4": (210.0, 297.0),
    "A0×2": (1189.0, 1682.0), "A0×3": (1189.0, 2523.0),
    "A1×3": (841.0, 1783.0), "A1×4": (841.0, 2378.0),
    "A2×3": (594.0, 1261.0), "A2×4": (594.0, 1682.0), "A2×5": (594.0, 2102.0),
    "A3×3": (420.0, 891.0), "A3×4": (420.0, 1189.0), "A3×5": (420.0, 1486.0),
    "A3×6": (420.0, 1783.0), "A3×7": (420.0, 2080.0),
    "A4×3": (297.0, 630.0), "A4×4": (297.0, 841.0), "A4×5": (297.0, 1051.0),
    "A4×6": (297.0, 1261.0), "A4×7": (297.0, 1471.0),
    "A4×8": (297.0, 1682.0), "A4×9": (297.0, 1892.0),
}

FORMAT_PATTERN = re.compile(r"[AА]\s*([0-4])(?!\d)(?:\s*[xх×*]\s*(\d{1,2})(?!\d))?", re.IGNORECASE)


def format_from_name(name: str) -> str | None:
    match = FORMAT_PATTERN.search(name)
    if not match:
        return None
    result = f"A{match.group(1)}"
    if match.group(2):
        result += f"×{int(match.group(2))}"
    return result if result in PAPER_SIZES else None


def page_format(width: float, height: float) -> str:
    shorter, longer = sorted((width, height))
    for name, (expected_shorter, expected_longer) in PAPER_SIZES.items():
        if abs(shorter - expected_shorter) <= 3 and abs(longer - expected_longer) <= 3:
            return name
    return f"CUSTOM {width:g}×{height:g} мм"
