"""Точка входа настольной оболочки DWG -> PDF."""

import sys
from pathlib import Path


def run_xml(filename: Path) -> int:
    from contextlib import redirect_stdout, redirect_stderr
    from xml.etree.ElementTree import ParseError
    from dwg_pdf.xml_job import read_xml_job

    def setup_error(error, code):
        message = f'Ошибка XML-задания: {error}\n'
        if sys.stderr is not None:
            sys.stderr.write(message)
        try:
            filename.expanduser().with_suffix('.error.log').write_text(message, encoding='utf-8')
        except OSError:
            pass
        return code

    try:
        options = read_xml_job(filename)
    except (OSError, ValueError, ParseError) as error:
        return setup_error(error, 2)
    try:
        options.output.mkdir(parents=True, exist_ok=True)
        with (options.output / 'conversion.log').open('w', encoding='utf-8') as log:
            with redirect_stdout(log), redirect_stderr(log):
                try:
                    from batch_convert import run_options
                    return run_options(options)
                except Exception as error:
                    print(f'Ошибка выполнения: {error}', flush=True)
                    return 1
    except OSError as error:
        return setup_error(error, 1)


if __name__ == "__main__":
    if len(sys.argv) == 2 and Path(sys.argv[1]).suffix.lower() == '.xml':
        raise SystemExit(run_xml(Path(sys.argv[1])))
    elif "--batch" in sys.argv:
        from batch_convert import run
        # Windowed Windows executables have no stdout/stderr. Keep a log
        # alongside the reports for unattended conversion and diagnostics.
        arguments = sys.argv[1:]
        arguments.remove("--batch")
        if sys.stdout is None:
            from pathlib import Path
            from batch_convert import parse_arguments
            output = parse_arguments(arguments).output.expanduser().resolve()
            output.mkdir(parents=True, exist_ok=True)
            with (output / "conversion.log").open("w", encoding="utf-8") as log:
                sys.stdout = sys.stderr = log
                raise SystemExit(run(arguments))
        raise SystemExit(run(arguments))
    else:
        from dwg_pdf.gui import run
        run()
