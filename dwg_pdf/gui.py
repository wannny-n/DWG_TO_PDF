"""Небольшой Tkinter-интерфейс без внешнего UI-фреймворка."""

from __future__ import annotations

import queue
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .discovery import discover_dwg_files
from .models import ConversionJob, JobStatus, RunSettings
from .oda_setup import (
    OdaDownloadResult,
    download_oda,
    find_installed_converter,
    launch_windows_installer,
    open_download_page,
    package_for_system,
)
from .paths import resource_path
from .service import ConversionService


class App(ttk.Frame):
    """Окно выбора DWG и наблюдения за последовательной очередью."""

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=16)
        self.master = master
        self.master.title("DWG Sheet Scanner — автономный PDF")
        self.master.minsize(1080, 650)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self._files: list[Path] = []
        self._folders: list[Path] = []
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._running = False

        self.output_dir = tk.StringVar(value=str(Path.cwd() / "output"))
        detected_converter = find_installed_converter()
        self.converter_exe = tk.StringVar(value=str(detected_converter) if detected_converter else "")
        # Ресурс включается в Linux- и Windows-сборки. Он не зависит от того,
        # из какой папки пользователь запустил приложение.
        self.font_file = tk.StringVar(value=str(resource_path("task", "GOST2304A.ttf")))
        self.include_model = tk.BooleanVar(value=True)
        self.include_layouts = tk.BooleanVar(value=True)
        self.merge = tk.BooleanVar(value=True)
        self.combined_pdf = tk.StringVar(value=str(Path.cwd() / "output" / "All_Selected_DWG.pdf"))

        self._build_settings()
        self._build_queue()
        self._build_footer()
        self.after(100, self._drain_events)

    def _build_settings(self) -> None:
        group = ttk.LabelFrame(self, text="Источник и автономный экспорт", padding=12)
        group.grid(row=0, column=0, sticky="ew")
        group.columnconfigure(1, weight=1)
        rows = [
            ("Папка результата", self.output_dir, self._choose_output),
            ("ODA File Converter (бесплатный DWG-читатель)", self.converter_exe, self._choose_converter),
            ("CAD-шрифты TTF/SHX (; через точку с запятой)", self.font_file, self._choose_font),
            ("Общий PDF", self.combined_pdf, self._choose_combined),
        ]
        for row, (label, variable, action) in enumerate(rows):
            ttk.Label(group, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            ttk.Entry(group, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=3)
            if action:
                ttk.Button(group, text="Выбрать…", command=action).grid(row=row, column=2, padx=(8, 0), pady=3)
        oda_controls = ttk.Frame(group)
        oda_controls.grid(row=4, column=1, sticky="w", pady=(6, 0))
        self.download_oda_button = ttk.Button(oda_controls, text="Скачать ODA для этой ОС", command=self._download_oda)
        self.download_oda_button.pack(side="left")
        self.detect_oda_button = ttk.Button(oda_controls, text="Найти установленный ODA", command=self._detect_oda)
        self.detect_oda_button.pack(side="left", padx=6)
        ttk.Label(
            group,
            text="nanoCAD не запускается и не требуется. Для точных надписей добавьте исходные TTF/SHX.",
        ).grid(row=5, column=1, sticky="w")
        ttk.Checkbutton(group, text="Искать рамки в ModelSpace", variable=self.include_model).grid(row=6, column=1, sticky="w")
        ttk.Checkbutton(group, text="Печатать готовые Layout", variable=self.include_layouts).grid(row=7, column=1, sticky="w")
        ttk.Checkbutton(group, text="Объединить все выбранные DWG в один PDF", variable=self.merge).grid(row=8, column=1, sticky="w")

    def _build_queue(self) -> None:
        group = ttk.LabelFrame(self, text="Очередь DWG", padding=12)
        group.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        group.rowconfigure(1, weight=1)
        group.columnconfigure(0, weight=1)
        controls = ttk.Frame(group)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(controls, text="Добавить DWG…", command=self._choose_files).pack(side="left")
        ttk.Button(controls, text="Добавить папку…", command=self._choose_folder).pack(side="left", padx=6)
        ttk.Button(controls, text="Очистить", command=self._clear).pack(side="left")

        columns = ("number", "file", "folder", "status", "sheets", "pdf", "message")
        self.tree = ttk.Treeview(group, columns=columns, show="headings", height=15)
        headings = {"number": "№", "file": "Файл", "folder": "Папка", "status": "Статус", "sheets": "Листы", "pdf": "PDF", "message": "Сообщение"}
        widths = {"number": 45, "file": 200, "folder": 220, "status": 145, "sheets": 60, "pdf": 110, "message": 300}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="w")
        self.tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(group, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

    def _build_footer(self) -> None:
        group = ttk.Frame(self, padding=(0, 12, 0, 0))
        group.grid(row=3, column=0, sticky="ew")
        self.start_button = ttk.Button(group, text="▶ Сканировать и создать PDF", command=self._start)
        self.start_button.pack(side="left")
        self.status = tk.StringVar(value="Добавьте DWG или папку с DWG")
        ttk.Label(group, textvariable=self.status).pack(side="left", padx=12)
        self.log = tk.Text(self, height=6, wrap="word", state="disabled")
        self.log.grid(row=4, column=0, sticky="ew")

    def _choose_files(self) -> None:
        selected = filedialog.askopenfilenames(title="Выберите DWG", filetypes=[("DWG", "*.dwg")])
        self._files.extend(Path(item) for item in selected)
        self._refresh_queue()

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(title="Выберите папку с DWG")
        if selected:
            self._folders.append(Path(selected))
        self._refresh_queue()

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(title="Папка результата")
        if selected:
            self.output_dir.set(selected)

    def _choose_converter(self) -> None:
        selected = filedialog.askopenfilename(
            title="ODA File Converter",
            filetypes=[("Исполняемый файл", "ODAFileConverter ODAFileConverter.exe *.exe"), ("Все файлы", "*")],
        )
        if selected:
            self.converter_exe.set(selected)

    def _detect_oda(self) -> None:
        """Подставляет ODA из PATH, стандартного пути или скачанного AppImage."""

        detected = find_installed_converter()
        if detected:
            self.converter_exe.set(str(detected))
            self._append_log(f"Найден ODA File Converter: {detected}")
            return
        messagebox.showinfo(
            "ODA не найден",
            "Нажмите «Скачать ODA для этой ОС» или укажите ODAFileConverter вручную.",
        )

    def _download_oda(self) -> None:
        """По явному действию пользователя скачивает только официальный пакет ODA."""

        try:
            package = package_for_system()
        except RuntimeError as error:
            self._append_log(str(error))
            open_download_page()
            return
        action = "скачать AppImage и подключить его" if package.is_portable else "скачать и открыть официальный MSI"
        if not messagebox.askyesno(
            "Скачать ODA File Converter",
            f"Будет выполнено действие: {action}. Продолжить?",
        ):
            return
        self.download_oda_button.state(["disabled"])
        self.detect_oda_button.state(["disabled"])
        threading.Thread(target=self._download_oda_worker, args=(package,), daemon=True).start()

    def _download_oda_worker(self, package: object) -> None:
        try:
            result = download_oda(package, lambda text: self._events.put(("log", text)))  # type: ignore[arg-type]
            self._events.put(("oda_downloaded", result))
        except Exception as error:
            self._events.put(("oda_download_error", str(error)))

    def _choose_font(self) -> None:
        selected = filedialog.askopenfilenames(
            title="Шрифты чертежа",
            filetypes=[("CAD-шрифты", "*.ttf *.otf *.shx"), ("Все файлы", "*")],
        )
        if selected:
            current = [item for item in self.font_file.get().split(";") if item.strip()]
            self.font_file.set(";".join(dict.fromkeys([*current, *selected])))

    def _font_paths(self) -> tuple[Path, ...]:
        """Разделитель ';' не конфликтует с пробелами в путях к CAD-шрифтам."""

        return tuple(
            Path(item.strip()).expanduser()
            for item in self.font_file.get().split(";")
            if item.strip()
        )

    def _choose_combined(self) -> None:
        selected = filedialog.asksaveasfilename(title="Общий PDF", defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if selected:
            self.combined_pdf.set(selected)

    def _clear(self) -> None:
        if self._running:
            return
        self._files.clear()
        self._folders.clear()
        self._refresh_queue()

    def _sources(self) -> list[Path]:
        return discover_dwg_files(self._files, self._folders)

    def _refresh_queue(self) -> None:
        if self._running:
            return
        self.tree.delete(*self.tree.get_children())
        for index, source in enumerate(self._sources(), 1):
            self.tree.insert("", "end", iid=str(source), values=(index, source.name, str(source.parent), JobStatus.QUEUED.value, "", "", "Ожидает запуска"))
        self.status.set(f"В очереди: {len(self._sources())} DWG")

    def _start(self) -> None:
        sources = self._sources()
        if not sources:
            messagebox.showwarning("Нет файлов", "Добавьте хотя бы один DWG-файл или папку с DWG.")
            return
        settings = RunSettings(
            output_dir=Path(self.output_dir.get()).expanduser(),
            converter_exe=Path(self.converter_exe.get()).expanduser() if self.converter_exe.get().strip() else None,
            font_files=self._font_paths(),
            include_model_frames=self.include_model.get(),
            include_layouts=self.include_layouts.get(),
            experimental_fallback=True,
            merge_to_one_pdf=self.merge.get(),
            combined_pdf=Path(self.combined_pdf.get()).expanduser(),
        )
        self._running = True
        self.start_button.state(["disabled"])
        self.status.set("Идёт обработка…")
        threading.Thread(target=self._run_worker, args=(settings, sources), daemon=True).start()

    def _run_worker(self, settings: RunSettings, sources: list[Path]) -> None:
        def log(text: str) -> None:
            self._events.put(("log", text))

        def changed(job: ConversionJob) -> None:
            self._events.put(("job", job))

        try:
            summary = ConversionService(settings, log, changed).run(sources)
            self._events.put(("done", summary))
        except Exception as error:
            self._events.put(("fatal", str(error)))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, value = self._events.get_nowait()
                if kind == "log":
                    self._append_log(str(value))
                elif kind == "job":
                    self._show_job(value)  # type: ignore[arg-type]
                elif kind == "done":
                    self._running = False
                    self.start_button.state(["!disabled"])
                    summary = value
                    self.status.set(f"Готово: {summary.succeeded}; предупреждений: {summary.warnings}; ошибок: {summary.failed}")
                elif kind == "fatal":
                    self._running = False
                    self.start_button.state(["!disabled"])
                    self.status.set("Запуск не выполнен")
                    self._append_log(str(value))
                    messagebox.showerror("Не удалось запустить", str(value))
                elif kind == "oda_downloaded":
                    self._show_oda_download_result(value)  # type: ignore[arg-type]
                elif kind == "oda_download_error":
                    self._finish_oda_download()
                    self._append_log(f"Не удалось скачать ODA: {value}")
                    messagebox.showerror("Не удалось скачать ODA", str(value))
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _show_oda_download_result(self, result: OdaDownloadResult) -> None:
        """Подключает Linux AppImage или передаёт MSI штатному установщику Windows."""

        self._finish_oda_download()
        if result.package.is_portable:
            self.converter_exe.set(str(result.path))
            messagebox.showinfo("ODA готов", "ODA AppImage скачан и подключён. Можно запускать экспорт.")
            return
        try:
            launch_windows_installer(result.path)
        except Exception as error:
            messagebox.showerror("Не удалось открыть MSI", str(error))
            return
        messagebox.showinfo(
            "Установите ODA",
            "Установщик ODA открыт. После завершения нажмите «Найти установленный ODA».",
        )

    def _finish_oda_download(self) -> None:
        self.download_oda_button.state(["!disabled"])
        self.detect_oda_button.state(["!disabled"])

    def _show_job(self, job: ConversionJob) -> None:
        iid = str(job.source)
        values = (job.position, job.source.name, str(job.source.parent), job.status.value, job.sheet_count, job.output_pdf.name if job.output_pdf else "", job.message)
        if self.tree.exists(iid):
            self.tree.item(iid, values=values)

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def run() -> None:
    """Создаёт главное окно только при явном запуске, что облегчает тестирование."""

    root = tk.Tk()
    App(root)
    root.mainloop()
