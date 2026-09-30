"""Предсказуемое формирование очереди DWG."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


def discover_dwg_files(files: Iterable[Path], folders: Iterable[Path]) -> list[Path]:
    """Возвращает уникальные существующие DWG в устойчивом алфавитном порядке.

    Папки обходятся рекурсивно. Результат не зависит от порядка, в котором ОС
    вернула элементы, поэтому номер листа и журнал воспроизводимы.
    """

    found: dict[str, Path] = {}

    def add(candidate: Path) -> None:
        if candidate.is_file() and candidate.suffix.casefold() == ".dwg":
            resolved = candidate.resolve()
            found.setdefault(str(resolved).casefold(), resolved)

    for source in files:
        add(source)
    for folder in folders:
        if not folder.is_dir():
            continue
        for candidate in folder.rglob("*"):
            add(candidate)

    return sorted(found.values(), key=lambda path: (path.name.casefold(), str(path).casefold()))
