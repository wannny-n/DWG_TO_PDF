"""Загрузка и обнаружение ODA File Converter на Windows и Linux.

Переносимый ODA ищется рядом с приложением. Дополнительное скачивание
происходит по кнопке пользователя и только по HTTPS с сайта ODA.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
from typing import Callable
from urllib.parse import quote
from urllib.request import Request, urlopen


Log = Callable[[str], None]

ODA_DOWNLOAD_ROOT = "https://www.opendesign.com/guestfiles/get?filename="
ODA_DOWNLOAD_PAGE = "https://www.opendesign.com/guestfiles/oda_file_converter?language=en"


@dataclass(frozen=True)
class OdaPackage:
    """Официальный пакет ODA для одной поддерживаемой ОС."""

    system: str
    filename: str
    is_portable: bool

    @property
    def url(self) -> str:
        return f"{ODA_DOWNLOAD_ROOT}{quote(self.filename)}"


@dataclass(frozen=True)
class OdaDownloadResult:
    """Результат скачивания; MSI запускается отдельно с подтверждением ОС."""

    package: OdaPackage
    path: Path


def package_for_system(system: str | None = None) -> OdaPackage:
    """Выбирает x64-пакет без привязки к конкретной версии ODA."""

    actual_system = system or platform.system()
    if actual_system == "Windows":
        return OdaPackage("Windows", "ODAFileConverter_QT6_vc16_amd64dll.msi", is_portable=False)
    if actual_system == "Linux":
        return OdaPackage("Linux", "ODAFileConverter_QT6_lnxX64_11dll.AppImage", is_portable=True)
    raise RuntimeError(f"Автозагрузка ODA пока поддерживает Windows и Linux, получено: {actual_system}")


def download_target(package: OdaPackage, home: Path | None = None) -> Path:
    """Выбирает пользовательскую папку, не требующую прав администратора."""

    user_home = home or Path.home()
    if package.is_portable:
        return user_home / ".local" / "share" / "DWG_Sheet_Scanner" / "ODAFileConverter.AppImage"
    return user_home / "Downloads" / "ODAFileConverter-setup.msi"


def download_oda(package: OdaPackage, log: Log, *, destination: Path | None = None) -> OdaDownloadResult:
    """Скачивает ODA атомарно и, для AppImage, делает файл исполняемым."""

    target = destination or download_target(package)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    request = Request(package.url, headers={"User-Agent": "DWG-Sheet-Scanner/1.0"})
    log(f"Скачивание ODA для {package.system}: {package.filename}")
    try:
        with urlopen(request, timeout=90) as response, temporary.open("wb") as stream:
            length = response.headers.get("Content-Length")
            expected = int(length) if length and length.isdigit() else None
            received = 0
            notified_at = 0
            while chunk := response.read(1024 * 1024):
                stream.write(chunk)
                received += len(chunk)
                if received - notified_at >= 10 * 1024 * 1024:
                    progress = f" из {expected // (1024 * 1024)} МБ" if expected else ""
                    log(f"Скачано {received // (1024 * 1024)} МБ{progress}")
                    notified_at = received
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    if package.is_portable:
        target.chmod(target.stat().st_mode | stat.S_IXUSR)
    log(f"ODA скачан: {target}")
    return OdaDownloadResult(package=package, path=target)


def launch_windows_installer(installer: Path) -> None:
    """Передаёт MSI оболочке Windows: UAC и условия установки подтверждает пользователь."""

    if platform.system() != "Windows":
        raise RuntimeError("MSI можно запустить только в Windows")
    os.startfile(str(installer))  # type: ignore[attr-defined]


def find_installed_converter(system: str | None = None, home: Path | None = None) -> Path | None:
    """Ищет переносимый ODA рядом с приложением, затем установленный ODA."""

    actual_system = system or platform.system()
    candidates: list[Path] = []
    from .paths import resource_path

    # Use the executable directory, never the process working directory:
    # shortcuts and network shares may launch the app from another folder.
    roots = [Path(sys.executable).resolve().parent] if getattr(sys, "frozen", False) else []
    roots.append(resource_path())
    executable_name = "ODAFileConverter.exe" if actual_system == "Windows" else "ODAFileConverter"
    for root in roots:
        candidates.append(root / "ODA" / executable_name)
        candidates.extend(sorted((root / "ODA").glob(f"ODAFileConverter*/{executable_name}"), reverse=True))
    environment_path = os.environ.get("ODA_FILE_CONVERTER")
    if environment_path:
        candidates.append(Path(environment_path))
    for executable in ("ODAFileConverter", "ODAFileConverter.exe"):
        discovered = shutil.which(executable)
        if discovered:
            candidates.append(Path(discovered))

    user_home = home or Path.home()
    if actual_system == "Linux":
        candidates.append(download_target(package_for_system("Linux"), user_home))
        candidates.extend(
            Path(location)
            for location in (
                "/opt/ODAFileConverter/ODAFileConverter",
                "/usr/local/bin/ODAFileConverter",
                "/usr/bin/ODAFileConverter",
            )
        )
    elif actual_system == "Windows":
        for root_name in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(root_name)
            if root:
                candidates.append(Path(root) / "ODA" / "ODAFileConverter" / "ODAFileConverter.exe")
                oda_root = Path(root) / "ODA"
                if oda_root.is_dir():
                    candidates.extend(sorted(
                        oda_root.glob("ODAFileConverter*/ODAFileConverter.exe"),
                        key=lambda path: tuple(int(part) for part in re.findall(r"\d+", path.parent.name)),
                        reverse=True,
                    ))

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def open_download_page() -> None:
    """Открывает официальный источник, если автоматический загрузчик недоступен."""

    import webbrowser

    webbrowser.open(ODA_DOWNLOAD_PAGE)
