"""Prepare complete screenshots for later annotation."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog, messagebox

import cv2
import ttkbootstrap as ttk
from PIL import Image, ImageTk

from crX.bot.coords import GAME_SCREEN_SIZE
from crX.detection.battlefield import battlefield_region
from crX.interface.battlefield_dedup_review import DedupReview
from crX.utils.battlefield_data import (
    MAX_FRAMES,
    AnnotationExport,
    BatchSearch,
    DeletedArchiveCleanup,
    FrameDeletion,
    PreparationSession,
    collect_screenshots,
    delete_frame,
    find_batches,
    get_battlefield_data_dir,
    purge_deleted_archives,
    validate_collection,
)
from crX.utils.battlefield_dedup import DeduplicationPlan, analyze_batches, export_selection
from crX.utils.open_folder import open_folder


class BattlefieldDataTab(ttk.Frame):
    def __init__(self, parent, *, busy_callback=None):
        super().__init__(parent, padding=12)
        self.busy_callback = busy_callback
        self.capture_callback = None
        self.busy = False
        self.running = False
        self.session = None
        self._dedup_plan = None
        self._dedup_keep = set()
        self._dedup_scanning = False
        self._review_window = None
        self.index = 0
        self.frame_image = None
        self._photos = []
        self._transform = None
        self._needs_preview = False
        self._preview_job = False
        self._results = Queue()
        self._stop = threading.Event()
        self._controls: list[
            tuple[ttk.Entry | ttk.Spinbox | ttk.Button | ttk.Menubutton | ttk.Combobox | ttk.Checkbutton, str]
        ] = []
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)

        ttk.Label(
            self,
            text="1 采集 / 打开批次 → 2 筛选图片 → 3 导出后标注（完整 720×1280 原图）",
            wraplength=720,
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        folder_row = ttk.Frame(self)
        folder_row.grid(row=1, column=0, sticky="ew")
        folder_row.columnconfigure(1, weight=1)
        ttk.Label(folder_row, text="保存目录").grid(row=0, column=0, padx=(0, 8))
        self.root_var = tk.StringVar(value=str(get_battlefield_data_dir() / "captures"))
        entry = ttk.Entry(folder_row, textvariable=self.root_var)
        entry.grid(row=0, column=1, sticky="ew")
        self._controls.append((entry, "normal"))
        self._button(folder_row, "打开", self._browse_root).grid(row=0, column=2, padx=6)

        dedup = ttk.Frame(self)
        dedup.grid(row=8, column=0, sticky="ew", pady=(8, 0))
        self._button(dedup, "筛选图片", self._analyze_all).pack(side="left")
        self.review_button = self._button(dedup, "查看筛选结果", self._review_duplicates)
        self.review_button.pack(side="left", padx=6)
        self.export_selected_button = self._button(dedup, "导出图片", self._export_selected)
        self.export_selected_button.configure(bootstyle="primary")
        self.export_selected_button.pack(side="left")
        self.dedup_status = tk.StringVar(value="先打开采集总目录，再筛选图片；原始图片保留。")
        ttk.Label(self, textvariable=self.dedup_status, wraplength=720).grid(row=9, column=0, sticky="ew", pady=4)

        sampling = ttk.Frame(self)
        sampling.grid(row=3, column=0, sticky="ew")
        self.interval_var = tk.StringVar(value="1")
        self.count_var = tk.StringVar(value="300")
        for label, variable, start, end in (
            ("采样间隔（秒）", self.interval_var, 0.2, 60),
            ("张数上限", self.count_var, 1, MAX_FRAMES),
        ):
            ttk.Label(sampling, text=label).pack(side="left", padx=(0, 6))
            spin = ttk.Spinbox(sampling, textvariable=variable, from_=start, to=end, width=6)
            spin.pack(side="left", padx=(0, 12))
            self._controls.append((spin, "normal"))

        actions = ttk.Frame(self)
        actions.grid(row=4, column=0, sticky="ew", pady=8)
        self.capture_button = self._button(actions, "开始连续采集", self._capture)
        self.capture_button.configure(bootstyle="primary")
        self.capture_button.grid(row=0, column=0, padx=(0, 6), pady=3)
        self._button(actions, "截取一张", lambda: self._capture(single=True)).grid(row=0, column=1, padx=6, pady=3)
        self.stop_button = ttk.Button(actions, text="停止", command=self.stop, state="disabled", bootstyle="danger")
        self.stop_button.grid(row=0, column=2, padx=6, pady=3)
        self.open_session_button = self._button(actions, "打开已有批次", self._open_session)
        self.open_session_button.grid(row=0, column=3, padx=6, pady=3)
        actions.columnconfigure(4, weight=1)
        self.more_menu = tk.Menu(self, tearoff=False)
        self.more_menu.add_command(label="清理已删除图片（整个采集目录）", command=self._purge_deleted)
        self.more_button = ttk.Menubutton(actions, text="更多", menu=self.more_menu, bootstyle="secondary")
        self.more_button.grid(row=0, column=4, sticky="e", padx=(6, 0), pady=3)
        self._controls.append((self.more_button, "normal"))
        self.batch_row = ttk.Frame(actions)
        self.batch_row.grid(row=1, column=0, columnspan=5, sticky="ew", pady=4)
        self.batch_row.columnconfigure(1, weight=1)
        ttk.Label(self.batch_row, text="找到的批次").grid(row=0, column=0, padx=(0, 6))
        self.batch_choice = ttk.Combobox(self.batch_row, state="readonly", width=1)
        self.batch_choice.grid(row=0, column=1, sticky="ew")
        self._controls.append((self.batch_choice, "readonly"))
        self.batch_choice.bind("<<ComboboxSelected>>", lambda _event: self._load_selected_batch())
        self._batch_paths = ()
        self._batch_root = None
        self.batch_row.grid_remove()

        previews = ttk.Frame(self)
        previews.grid(row=5, column=0, sticky="nsew")
        previews.columnconfigure(0, weight=1)
        previews.rowconfigure(0, weight=1)
        self.raw_canvas = tk.Canvas(previews, highlightthickness=0, width=270, height=300)
        self.raw_canvas.grid(row=0, column=0, sticky="nsew")
        self.raw_canvas.bind("<Configure>", lambda _event: self._draw())
        self.bind("<<ThemeChanged>>", lambda _event: self._draw(), add="+")

        footer = ttk.Frame(self)
        footer.grid(row=6, column=0, sticky="ew", pady=(6, 0))
        self._button(footer, "上一张", lambda: self._navigate(-1)).pack(side="left")
        self._button(footer, "下一张", lambda: self._navigate(1)).pack(side="left", padx=6)
        delete_button = self._button(footer, "删除当前张", self._delete_current)
        delete_button.configure(bootstyle="danger")
        delete_button.pack(side="right")
        self.position_var = tk.StringVar(value="尚未采集")
        ttk.Label(footer, textvariable=self.position_var).pack(side="left", padx=6)
        self.message_var = tk.StringVar(value="每批只采集一场；先连接设备，或打开已有批次。")
        ttk.Label(self, textvariable=self.message_var, wraplength=720).grid(row=7, column=0, sticky="ew", pady=(6, 0))
        self._update_controls()
        self._poll_id = self.after(100, self._poll)

    def _button(self, parent, text, command):
        widget = ttk.Button(parent, text=text, command=command, bootstyle="secondary")
        self._controls.append((widget, "normal"))
        return widget

    def set_running(self, running):
        self.running = running
        self._update_controls()

    def _update_controls(self):
        for widget, normal in self._controls:
            widget.configure(state="disabled" if self.busy or self.running else normal)
        self.stop_button.configure(
            state="normal" if self.busy and not self._preview_job and not self._stop.is_set() else "disabled"
        )

        available = not self.busy and not self.running and self._dedup_plan is not None
        self.review_button.configure(state="normal" if available and self._dedup_plan.groups else "disabled")
        self.export_selected_button.configure(state="normal" if available and self._dedup_keep else "disabled")

    def _invalidate_dedup(self):
        self._dedup_plan = None
        self._dedup_keep = set()
        self.dedup_status.set("采集来源已更新，请重新筛选图片。")
        self._update_controls()

    def _dedup_summary(self):
        plan = self._dedup_plan
        if plan is None:
            return
        near = sum(group.kind != "exact" for group in plan.groups)
        self.dedup_status.set(
            f"共 {len(plan.sessions)} 批 / {len(plan.images)} 张 · 完全重复 {plan.exact_duplicates} 张 · "
            f"相似场景 {near} 组 · 将导出 {len(plan.default_keep)} 张"
        )
        self._update_controls()

    def _analyze_all(self):
        if self.busy or self.running:
            return
        text = self.root_var.get().strip()
        if self._batch_root is None and not text:
            self.message_var.set("请先打开采集目录或填写保存目录。")
            return
        source = self._batch_root or Path(text).expanduser()
        self._invalidate_dedup()
        self._dedup_scanning = True
        self.dedup_status.set(f"正在分析全部批次：{source}")
        self._start(
            lambda: analyze_batches(
                source,
                stop=self._stop,
                progress=lambda message: self._results.put(("dedup_progress", message)),
            )
        )

    def _review_duplicates(self):
        if self.busy or self.running or self._dedup_plan is None:
            return
        self._review_window = DedupReview(self, self._dedup_plan)

    def _export_selected(self):
        if self.busy or self.running or self._dedup_plan is None:
            return
        destination = filedialog.askdirectory(
            parent=self,
            title="选择图片导出目录（只生成平铺的 images）",
            initialdir=str(get_battlefield_data_dir()),
        )
        if not destination:
            return
        plan = self._dedup_plan
        keep = set(plan.default_keep)
        self._start(
            lambda: export_selection(
                plan,
                keep,
                Path(destination),
                stop=self._stop,
                progress=lambda count: self._results.put(("annotation_progress", count)),
            )
        )

    def stop(self):
        self._stop.set()
        self.stop_button.configure(state="disabled")
        if self.busy:
            self.message_var.set("正在停止，已保存的图片会保留；等待当前设备调用完成。")

    def destroy(self):
        self._stop.set()
        self.after_cancel(self._poll_id)
        super().destroy()

    def _input_bounds(self):
        return battlefield_region(self._screen_size())

    def _screen_size(self):
        return tuple(self.session.metadata["screen_size"]) if self.session is not None else GAME_SCREEN_SIZE

    def _collection_options(self):
        root_text = self.root_var.get().strip()
        if not root_text:
            raise ValueError("请选择保存目录")
        try:
            count, interval = int(self.count_var.get()), float(self.interval_var.get())
        except ValueError as error:
            raise ValueError("请输入有效的采样间隔和张数上限") from error
        validate_collection(count, interval)
        crop = self._input_bounds()
        size = self._screen_size()
        native_crop = tuple(
            round(value * GAME_SCREEN_SIZE[index % 2] / size[index % 2]) for index, value in enumerate(crop)
        )
        return Path(root_text).expanduser(), native_crop, count, interval

    def _browse_root(self):
        if self.busy or self.running:
            return
        target = self.root_var.get().strip()
        if not target:
            self.message_var.set("请填写保存目录")
            return
        self._start(lambda: open_folder(target), preview=True)
        self.message_var.set(f"已请求打开保存目录：{target}")

    def _start(self, job, *, preview=False):
        if self.busy or self.running:
            return
        self.busy = True
        self._preview_job = preview
        if not preview:
            self._stop.clear()
        self._update_controls()
        if self.busy_callback:
            self.busy_callback()
        if not preview:
            self.message_var.set("正在处理，可点击停止；不会操作游戏内按钮。")

        def run():
            try:
                self._results.put(("done", job()))
            except Exception as error:
                self._results.put(("error", str(error)))

        threading.Thread(target=run, name="battlefield-data", daemon=True).start()

    def _publish(self, session):
        self._results.put(("session", session))

    def _progress(self, count):
        self._results.put(("progress", count))

    def _capture(self, single=False):
        if self.busy or self.running:
            return
        try:
            root, crop, count, interval = self._collection_options()
            if self.capture_callback is None:
                raise ValueError("设备采集尚未连接")
            device_factory = self.capture_callback()
        except (ValueError, OSError) as error:
            self.message_var.set(str(error))
            return

        def job():
            session = PreparationSession.create(root, crop)
            self._publish(session)
            return collect_screenshots(
                session,
                device_factory,
                count=1 if single else count,
                interval=interval,
                stop=self._stop,
                progress=self._progress,
                skip_similar=not single,
            )

        self._invalidate_dedup()
        self._start(job)

    def _open_session(self):
        if self.busy or self.running:
            return
        source = filedialog.askdirectory(
            parent=self,
            title="选择采集目录（自动查找其中及子文件夹中的批次）",
            initialdir=str(Path(self.root_var.get()).expanduser()),
        )
        if source:
            self._invalidate_dedup()
            self._start(lambda: find_batches(Path(source), stop=self._stop, cleanup_empty=True))
            self.message_var.set(f"正在查找采集批次：{source}，可点击停止。")

    def _show_batches(self, result):
        if self._stop.is_set():
            self.message_var.set("已停止查找批次，当前图片保留。")
            return
        self._batch_paths = result.directories
        self._batch_root = result.root
        self.batch_choice.configure(values=[str(path.relative_to(result.root)) for path in result.directories])
        summary = f"已清理 {result.removed} 个空批次。" if result.removed else ""
        if result.warnings:
            summary += "\n" + "\n".join(result.warnings[:3])
        if summary:
            self.dedup_status.set(summary)
        if not result.directories:
            self.batch_choice.set("")
            self.batch_row.grid_remove()
            self.session = None
            self.index = 0
            self.frame_image = None
            self._needs_preview = False
            self.position_var.set("暂无图片")
            self._draw()
            self.message_var.set(summary + "没有可加载的非空批次。")
            return
        self.batch_choice.current(0)
        if len(result.directories) == 1:
            self.batch_row.grid_remove()
        else:
            self.batch_row.grid()
        self._load_selected_batch()

    def _load_selected_batch(self):
        if self.busy or self.running:
            return
        index = self.batch_choice.current()
        if 0 <= index < len(self._batch_paths):
            path = self._batch_paths[index]
            root = self._batch_root or path.parent

            def job():
                session = PreparationSession.open(path)
                if not session.metadata["frames"]:
                    return find_batches(root, stop=self._stop, cleanup_empty=True)
                self._publish(session)
                return None

            self._start(job)

    def _delete_current(self):
        if self.busy or self.running:
            return
        if self.session is None or not self.session.metadata["frames"]:
            self.message_var.set("请先打开要删除的图片。")
            return
        session, index = self.session, self.index
        self._start(lambda: delete_frame(session, index))

    def _purge_deleted(self):
        if self.busy or self.running:
            return
        target = self.root_var.get().strip()
        if not target:
            self.message_var.set("请填写保存目录")
            return
        root = Path(target).expanduser().absolute()
        if messagebox.askyesno(
            "清理已删除图片",
            f"永久清理此目录及子目录中所有采集批次的已删除图片？\n{root}",
            parent=self,
        ):
            current = self.session
            self._start(lambda: purge_deleted_archives(root, current), preview=True)
            self.message_var.set(f"正在清理已删除图片：{root}")

    def _navigate(self, offset):
        if self.session is not None and self.session.metadata["frames"]:
            self.index = min(max(0, self.index + offset), len(self.session.metadata["frames"]) - 1)
            self._load_preview()

    def _load_preview(self):
        session, index = self.session, self.index
        if session is None or not session.metadata["frames"]:
            self.frame_image = None
            self.position_var.set("暂无图片")
            self._draw()
            return

        def job():
            self._results.put(("preview", session.frame(index)))

        self._start(job, preview=True)

    def _poll(self):
        try:
            while True:
                kind, value = self._results.get_nowait()
                if kind == "session":
                    self.session, self.index = value, 0
                    if value.directory not in self._batch_paths:
                        self._batch_root = value.directory.parent
                        self._batch_paths = ()
                        self.batch_row.grid_remove()
                    self.frame_image = None
                    self._needs_preview = True
                    self.position_var.set("暂无图片")
                    self._draw()
                elif kind == "progress":
                    self.message_var.set(f"已保存 {value} 张完整截图。")
                elif kind == "dedup_progress":
                    self.dedup_status.set(value)
                elif kind == "annotation_progress":
                    self.message_var.set(f"正在准备 {value} 张完整标注图片。")
                elif kind == "preview":
                    self.frame_image = value
                    self._draw()
                else:
                    self.busy = False
                    self._update_controls()
                    if self.busy_callback:
                        self.busy_callback()
                    if kind == "error":
                        if self._dedup_scanning:
                            self._dedup_scanning = False
                            self.dedup_status.set(
                                "已停止图片筛选，原始图片保留。"
                                if self._stop.is_set()
                                else "筛选未完成，请检查提示后重试。"
                            )
                        self.message_var.set(f"处理未完成：{value}。已保存的图片保留。")
                    elif isinstance(value, DeduplicationPlan):
                        self._dedup_scanning = False
                        self._dedup_plan = value
                        self._dedup_keep = set(value.default_keep)
                        self._dedup_summary()
                        self.message_var.set("图片筛选完成：已按规则保留代表帧，可直接导出图片，也可查看筛选结果。")
                    elif isinstance(value, AnnotationExport):
                        self.message_var.set(
                            f"{'已停止' if self._stop.is_set() else '整理完成'}：新增 {value.count} 张，"
                            f"已有相同图片跳过 {value.skipped} 张，筛选排除 {value.excluded} 张 · {value.directory}"
                        )
                    elif isinstance(value, DeletedArchiveCleanup):
                        self.session = value.session
                        summary = f"已清理 {value.count} 个批次的已删除图片。"
                        if value.warnings:
                            summary += f" {len(value.warnings)} 个批次处理失败，首个错误：{value.warnings[0]}"
                        self.message_var.set(summary)
                    elif isinstance(value, FrameDeletion):
                        self._invalidate_dedup()
                        self._needs_preview = False
                        self.frame_image = None
                        if value.session.metadata["frames"]:
                            self.session, self.index = value.session, value.index
                            self._load_preview()
                            self.message_var.set("已删除当前张，原文件保存在该批次的 .deleted 中，可恢复。")
                        else:
                            directory = value.session.directory
                            root = self._batch_root
                            if root is None or root == directory:
                                root = directory.parent
                            paths = tuple(path for path in self._batch_paths if path != directory)
                            self._show_batches(BatchSearch(root, paths))
                            self.message_var.set(
                                f"已删除最后一张；空批次清理未完成：{value.cleanup_error}"
                                if value.cleanup_error
                                else "已删除最后一张，并清理该空批次文件夹。"
                            )
                    elif isinstance(value, BatchSearch):
                        self._show_batches(value)
                    elif self.session is not None:
                        count = len(self.session.metadata["frames"])
                        self.position_var.set(f"{self.index + 1 if count else 0} / {count} 张")
                        if not self._preview_job:
                            skipped = f" · 跳过 {value} 张相似截图" if value else ""
                            self.message_var.set(
                                f"{'已停止' if self._stop.is_set() else '已完成'} · 保存 {count} 张{skipped} · {self.session.directory}"
                            )
                    # A completed capture/open needs one off-thread read of its first frame.
                    if (
                        self.session is not None
                        and self._needs_preview
                        and self.session.metadata["frames"]
                        and kind != "error"
                    ):
                        self._needs_preview = False
                        self._load_preview()
        except Empty:
            pass
        self._poll_id = self.after(100, self._poll)

    def _draw_image(self, canvas, image):
        width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        scale = min(width / image.width, height / image.height)
        size = max(1, round(image.width * scale)), max(1, round(image.height * scale))
        photo = ImageTk.PhotoImage(image.resize(size, Image.Resampling.LANCZOS), master=self)
        self._photos.append(photo)
        x, y = (width - size[0]) / 2, (height - size[1]) / 2
        canvas.create_image(x, y, image=photo, anchor="nw")
        return x, y, size[0] / image.width, size[1] / image.height

    def _draw(self):
        self._photos.clear()
        self.raw_canvas.delete("all")
        colors = ttk.Style().colors
        self.raw_canvas.configure(background=colors.inputbg)
        self._transform = None
        if self.frame_image is None:
            self.raw_canvas.create_text(
                max(1, self.raw_canvas.winfo_width()) // 2,
                max(1, self.raw_canvas.winfo_height()) // 2,
                text="还没有图片\n连接设备后截取一张，或打开已有批次。",
                width=max(1, self.raw_canvas.winfo_width() - 32),
                fill=colors.inputfg,
                justify="center",
            )
            return
        image = Image.fromarray(cv2.cvtColor(self.frame_image, cv2.COLOR_BGR2RGB))
        self._transform = self._draw_image(self.raw_canvas, image)
