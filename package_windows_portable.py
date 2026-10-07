"""Создаёт Windows ZIP с app-local ODA из предоставленной папки.

Используйте только при наличии прав на использование и распространение ODA.
Файлы ODA не скачиваются и не добавляются в Git.
"""
from pathlib import Path
import argparse
import os
import shutil
import zipfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--oda-dir', type=Path, required=True)
    parser.add_argument('--app-dir', type=Path, default=Path('dist/DWG_Sheet_Scanner'))
    parser.add_argument('--runtime-dir', type=Path, default=Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32')
    parser.add_argument('--output', type=Path, default=Path('dist/DWG_Sheet_Scanner_Windows_x64_Portable_ODA.zip'))
    args = parser.parse_args()
    if not (args.app_dir / 'DWG_Sheet_Scanner.exe').is_file():
        parser.error('Сначала соберите Windows-приложение.')
    if not (args.oda_dir / 'ODAFileConverter.exe').is_file():
        parser.error('--oda-dir должен указывать на полную папку ODA File Converter x64.')
    # Qt6 also needs these VC runtime DLLs. Keep the complete matching
    # x64 runtime set app-local; do not require an administrator installer.
    runtimes = ('concrt140.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
                'vccorlib140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')
    for name in runtimes:
        if not (args.runtime_dir / name).is_file():
            parser.error(f'Не найдена x64 библиотека {name}; укажите --runtime-dir.')
    target = args.app_dir / 'ODA'
    if target.exists():
        parser.error('Папка ODA уже существует в сборке. Используйте свежую сборку.')
    shutil.copytree(args.oda_dir, target)
    for name in runtimes:
        shutil.copy2(args.runtime_dir / name, target / name)
    for name in ('ИНСТРУКЦИЯ.txt', 'ODA_NOTICE.txt'):
        shutil.copy2(Path(__file__).resolve().parent / name, args.app_dir / name)
    shutil.copy2(Path(__file__).resolve().parent / 'DISTRIBUTION.md', args.app_dir / 'README.md')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(args.app_dir.rglob('*')):
            if path.is_file():
                archive.write(path, Path('DWG_Sheet_Scanner') / path.relative_to(args.app_dir))
    with zipfile.ZipFile(args.output) as archive:
        error = archive.testzip()
        if error:
            raise RuntimeError(f'Повреждённый файл в ZIP: {error}')
    print(f'Portable ZIP: {args.output.resolve()}')


if __name__ == '__main__':
    main()
