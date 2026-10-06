"""Точка входа настольной оболочки DWG -> PDF."""

import sys


if __name__ == "__main__":
    if "--batch" in sys.argv:
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
