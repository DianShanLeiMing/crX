"""Manual YOLO annotation loading and battle-grouped dataset export."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog

import ttkbootstrap as ttk

from crX.utils.battlefield_data import get_battlefield_data_dir
from crX.utils.dataset_split import (
    CLASS_LIST_GUIDE,
    ClassListFormatError,
    DatasetCancelledError,
    describe_plan,
    ensure_annotation_inbox,
    export_split,
    plan_split,
    scan_annotations,
)
from crX.utils.open_folder import open_folder, open_text_file


class DatasetSplitTab(ttk.Frame):
    def __init__(self, parent, *, busy_callback=None):
        super().__init__(parent, padding=12)
        self.busy_callback = busy_callback
        self.busy = False
        self.running = False
        self.plan = None
        self.output = None
        self._results = Queue()
        self._stop = threading.Event()
        self._controls: list[tuple[ttk.Entry | ttk.Spinbox | ttk.Button, str]] = []
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        self.inbox = get_battlefield_data_dir() / "annotated"
        self.source_var = tk.StringVar(value=str(self.inbox))
        self.classes_var = tk.StringVar(value=str(self.inbox / "classes.txt"))
        self.destination_var = tk.StringVar(value=str(get_battlefield_data_dir() / "datasets"))
        self.percent_var = tk.StringVar(value="20")
        self.message_var = tk.StringVar(value="选择标注目录后自动载入；同一场的全部图片保持在同一集合。")

        ttk.Label(
            self,
            text="标注工具导出 YOLO 检测格式后，选择目录载入。\n按场次子目录划分，图片与标签必须同名；至少需要两场独立对局。",
            wraplength=680,
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        paths = ttk.Frame(self)
        paths.grid(row=1, column=0, sticky="ew")
        paths.columnconfigure(1, weight=1)
        for row, (label, variable, command) in enumerate(
            (
                ("标注目录", self.source_var, self._browse_source),
                ("类别表", self.classes_var, self._browse_classes),
                ("输出目录", self.destination_var, self._browse_destination),
            )
        ):
            ttk.Label(paths, text=label, width=9).grid(row=row, column=0, sticky="w", pady=4)
            entry = ttk.Entry(paths, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            self._controls.append((entry, "normal"))
            self._button(paths, "选择并载入" if row == 0 else "打开", command).grid(row=row, column=2, padx=(8, 0))

        options = ttk.Frame(self)
        options.grid(row=2, column=0, sticky="ew", pady=8)
        ttk.Label(options, text="验证集比例（%）").pack(side="left", padx=(0, 6))
        spin = ttk.Spinbox(options, textvariable=self.percent_var, from_=1, to=99, width=8)
        spin.pack(side="left", padx=(0, 12))
        self._controls.append((spin, "normal"))
        ttk.Label(options, text="默认训练 80% / 验证 20%，按整场分配").pack(side="left")

        actions = ttk.Frame(self)
        actions.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self._button(actions, "打开默认导入目录", self._open_inbox).grid(row=0, column=0, padx=(0, 6), pady=4)
        self.load_button = self._button(actions, "载入并检查", self._load)
        self.load_button.grid(row=0, column=1, padx=6, pady=4)
        self.export_button = self._button(actions, "导出训练／验证集", self._export)
        self.export_button.grid(row=0, column=2, padx=6, pady=4)
        self.stop_button = ttk.Button(
            actions, text="停止", command=self.stop, state="disabled", bootstyle="danger-outline"
        )
        self.stop_button.grid(row=0, column=3, padx=6, pady=4)
        self.open_output_button = self._button(actions, "打开导出目录", self._open_output)
        self.open_output_button.grid(row=1, column=0, padx=(0, 6), pady=4)
        ttk.Label(actions, text="输出 data.yaml，供 YOLO26 目标检测训练使用。", wraplength=450).grid(
            row=1, column=1, columnspan=3, sticky="w", padx=6
        )

        report = ttk.Frame(self)
        report.grid(row=4, column=0, sticky="nsew")
        report.columnconfigure(0, weight=1)
        report.rowconfigure(0, weight=1)
        scrollbar = ttk.Scrollbar(report)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.report = tk.Text(report, state="disabled", wrap="word", height=10, width=50, yscrollcommand=scrollbar.set)
        self.report.grid(row=0, column=0, sticky="nsew")
        scrollbar.configure(command=self.report.yview)
        self._show_report(
            "建议目录（也可在任意位置保持同样结构）：\n"
            f"{self.inbox}\n"
            "  classes.txt\n"
            "  images/场次001/000000.png\n"
            "  labels/场次001/000000.txt\n"
            "  images/场次002/000000.png\n"
            "  labels/场次002/000000.txt\n\n" + CLASS_LIST_GUIDE + "\n"
            "类别编号从 0 开始，顺序必须与标注工具一致。\n"
            "没有目标的图片使用空 txt。标注完成后保持图片名称和裁剪范围。\n"
            "同一场的不同片段也放在同一场次目录；程序不会根据数字文件名猜测场次。"
        )
        ttk.Label(self, textvariable=self.message_var, wraplength=680).grid(row=5, column=0, sticky="ew", pady=(8, 0))
        self._update_controls()
        for variable in (self.source_var, self.classes_var, self.percent_var):
            variable.trace_add("write", self._invalidate)
        self._poll_id = self.after(100, self._poll)

    def _button(self, parent, text, command):
        button = ttk.Button(parent, text=text, command=command)
        self._controls.append((button, "normal"))
        return button

    def _invalidate(self, *_args):
        self.plan = None
        self._update_controls()

    def _show_report(self, text):
        self.report.configure(state="normal")
        self.report.delete("1.0", "end")
        self.report.insert("1.0", text)
        self.report.configure(state="disabled")

    def set_running(self, running):
        self.running = running
        self._update_controls()

    def _update_controls(self):
        blocked = self.busy or self.running
        for widget, state in self._controls:
            widget.configure(state="disabled" if blocked else state)
        self.export_button.configure(state="normal" if self.plan is not None and not blocked else "disabled")
        self.open_output_button.configure(state="normal" if self.output is not None and not blocked else "disabled")
        self.stop_button.configure(state="normal" if self.busy and not self._stop.is_set() else "disabled")

    def stop(self):
        self._stop.set()
        self.stop_button.configure(state="disabled")
        if self.busy:
            self.message_var.set("正在停止；原始标注数据保留，未完成的导出不会作为数据集发布。")

    def destroy(self):
        self._stop.set()
        self.after_cancel(self._poll_id)
        super().destroy()

    def _browse_source(self):
        value = filedialog.askdirectory(parent=self, title="选择包含 images、labels、classes.txt 的标注目录")
        if value:
            self.source_var.set(value)
            self.classes_var.set(str(Path(value) / "classes.txt"))
            self._load()

    def _browse_classes(self):
        if self.busy or self.running:
            return
        target = self.classes_var.get().strip()
        if not target:
            self.message_var.set("请填写类别表文件路径")
            return

        def job():
            open_text_file(target)
            self._results.put(("message", f"已请求打开类别表：{target}"))

        self._start(job)

    def _browse_destination(self):
        if self.busy or self.running:
            return
        target = self.destination_var.get().strip()
        if not target:
            self.message_var.set("请填写输出目录")
            return

        def job():
            open_folder(target)
            self._results.put(("message", f"已请求打开输出目录：{target}"))

        self._start(job)

    def _open_inbox(self):
        self.source_var.set(str(self.inbox))
        self.classes_var.set(str(self.inbox / "classes.txt"))

        def job():
            ensure_annotation_inbox(self.inbox)
            open_folder(str(self.inbox))
            self._results.put(("message", f"将标注导出到 {self.inbox}，也可以选择其他目录载入。"))

        self._start(job)

    def _settings(self):
        if not self.source_var.get().strip() or not self.classes_var.get().strip():
            raise ValueError("请选择标注目录和类别表")
        try:
            percent = int(self.percent_var.get())
        except ValueError as error:
            raise ValueError("验证集比例必须是整数") from error
        return Path(self.source_var.get().strip()), Path(self.classes_var.get().strip()), percent

    def _load(self):
        if self.busy or self.running:
            return
        try:
            source, classes, percent = self._settings()
        except ValueError as error:
            self.message_var.set(str(error))
            return
        self.plan = None

        def job():
            scan = scan_annotations(source, classes, stop=self._stop, progress=self._progress)
            self._results.put(("plan", plan_split(scan, percent)))

        self._start(job)

    def _export(self):
        if self.plan is None or self.busy or self.running:
            return
        destination = self.destination_var.get().strip()
        if not destination:
            self.message_var.set("请选择输出目录")
            return
        plan = self.plan

        def job():
            output = export_split(plan, Path(destination), stop=self._stop, progress=self._progress)
            self._results.put(("output", output))

        self._start(job)

    def _open_output(self):
        if self.output is not None:
            path = str(self.output)
            self._start(lambda: open_folder(path))

    def _progress(self, text):
        self._results.put(("message", text))

    def _start(self, job):
        if self.busy or self.running:
            return
        self.busy = True
        self._stop.clear()
        self._update_controls()
        self.message_var.set("正在处理标注数据……")
        if self.busy_callback:
            self.busy_callback()

        def run():
            try:
                job()
            except DatasetCancelledError:
                self._results.put(("message", "已停止；原始标注数据保留。"))
            except ClassListFormatError as error:
                self._results.put(("classes_error", str(error)))
            except Exception as error:
                self._results.put(("error", str(error)))
            finally:
                self._results.put(("done", None))

        threading.Thread(target=run, name="dataset-split", daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, value = self._results.get_nowait()
                if kind == "plan":
                    self.plan = value
                    self._show_report(describe_plan(value))
                    self.message_var.set("检查完成，可导出训练／验证集。同场图片不会跨集合。")
                elif kind == "output":
                    self.output = value
                    self.message_var.set(f"已导出：{value}")
                elif kind == "message":
                    self.message_var.set(value)
                elif kind in {"error", "classes_error"}:
                    self.plan = None
                    details = f"未能完成，请检查：\n{value}"
                    if kind == "classes_error":
                        details += f"\n\n{CLASS_LIST_GUIDE}"
                    self._show_report(details)
                    self.message_var.set(str(value))
                elif kind == "done":
                    self.busy = False
                    self._update_controls()
                    if self.busy_callback:
                        self.busy_callback()
        except Empty:
            pass
        self._poll_id = self.after(100, self._poll)
