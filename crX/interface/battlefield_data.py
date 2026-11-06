"""Prepare screenshots and crops for later battlefield annotation."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog

import cv2
import ttkbootstrap as ttk
from PIL import Image, ImageTk

from crX.bot.coords import BATTLEFIELD_DATA_REGION_LTRB, GAME_SCREEN_SIZE
from crX.utils.battlefield_data import (
    MAX_FRAMES,
    PreparationSession,
    collect_screenshots,
    get_battlefield_data_dir,
    import_screenshots,
    recrop_session,
    validate_collection,
    validate_crop,
)
from crX.utils.open_folder import open_folder


class BattlefieldDataTab(ttk.Frame):
    def __init__(self, parent, *, busy_callback=None):
        super().__init__(parent, padding=12)
        self.busy_callback = busy_callback
        self.capture_callback = None
        self.busy = False
        self.running = False
        self.session = None
        self.index = 0
        self.frame_image = None
        self._photos = []
        self._transform = None
        self._drag_start = None
        self._needs_preview = False
        self._preview_job = False
        self._crop_preview_id = None
        self._results = Queue()
        self._stop = threading.Event()
        self._controls: list[tuple[ttk.Entry | ttk.Spinbox | ttk.Button | ttk.Menubutton | ttk.Checkbutton, str]] = []
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)

        ttk.Label(
            self,
            text="手动进入对战或回放，每批只采集一场。采集后拖框裁剪，再保存裁剪结果。",
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

        crop_row = ttk.Frame(self)
        crop_row.grid(row=2, column=0, sticky="ew", pady=8)
        self.crop_vars = []
        for label, value in zip(("左", "上", "右", "下"), BATTLEFIELD_DATA_REGION_LTRB, strict=True):
            ttk.Label(crop_row, text=label).pack(side="left", padx=(0, 4))
            variable = tk.StringVar(value=str(value))
            self.crop_vars.append(variable)
            entry = ttk.Entry(crop_row, textvariable=variable, width=5)
            entry.pack(side="left", padx=(0, 10))
            self._controls.append((entry, "normal"))
        self._button(crop_row, "恢复默认", self._reset_crop).pack(side="left", padx=4)

        sampling = ttk.Frame(self)
        sampling.grid(row=3, column=0, sticky="ew")
        self.interval_var = tk.StringVar(value="1")
        self.count_var = tk.StringVar(value="120")
        self.skip_similar_var = tk.BooleanVar(value=True)
        for label, variable, start, end in (
            ("采样间隔（秒）", self.interval_var, 0.2, 60),
            ("张数上限", self.count_var, 1, MAX_FRAMES),
        ):
            ttk.Label(sampling, text=label).pack(side="left", padx=(0, 6))
            spin = ttk.Spinbox(sampling, textvariable=variable, from_=start, to=end, width=6)
            spin.pack(side="left", padx=(0, 12))
            self._controls.append((spin, "normal"))
        skip_similar = ttk.Checkbutton(sampling, text="跳过相似截图", variable=self.skip_similar_var)
        skip_similar.pack(side="left")
        self._controls.append((skip_similar, "normal"))

        actions = ttk.Frame(self)
        actions.grid(row=4, column=0, sticky="ew", pady=8)
        self._button(actions, "截取一张", lambda: self._capture(single=True)).grid(row=0, column=0, padx=(0, 6), pady=3)
        self.capture_button = self._button(actions, "开始连续采集", self._capture)
        self.capture_button.grid(row=0, column=1, padx=6, pady=3)
        self.stop_button = ttk.Button(
            actions, text="停止", command=self.stop, state="disabled", bootstyle="danger-outline"
        )
        self.stop_button.grid(row=0, column=2, padx=6, pady=3)
        self._button(actions, "保存裁剪结果", self._recrop).grid(row=0, column=3, padx=6, pady=3)
        self.more_button = ttk.Menubutton(actions, text="更多")
        self.more_button.grid(row=0, column=4, padx=6, pady=3)
        self.more_menu = tk.Menu(self.more_button, tearoff=False)
        self.more_menu.add_command(label="导入图片或视频…", command=self._import_file)
        self.more_menu.add_command(label="导入图片目录或录制包…", command=self._import_folder)
        self.more_menu.add_command(label="打开已有批次…", command=self._open_session)
        self.more_button.configure(menu=self.more_menu)
        self._controls.append((self.more_button, "normal"))

        previews = ttk.Frame(self)
        previews.grid(row=5, column=0, sticky="nsew")
        previews.columnconfigure((0, 1), weight=1, uniform="preview")
        previews.rowconfigure(1, weight=1)
        ttk.Label(previews, text="原图：可拖动框选裁剪区域").grid(row=0, column=0)
        ttk.Label(previews, text="裁剪预览（仅显示缩放，保存不缩放）").grid(row=0, column=1)
        self.raw_canvas = tk.Canvas(previews, background="#202428", highlightthickness=0, width=270, height=300)
        self.crop_canvas = tk.Canvas(previews, background="#202428", highlightthickness=0, width=270, height=300)
        self.raw_canvas.grid(row=1, column=0, sticky="nsew", padx=(0, 4))
        self.crop_canvas.grid(row=1, column=1, sticky="nsew", padx=(4, 0))
        for canvas in (self.raw_canvas, self.crop_canvas):
            canvas.bind("<Configure>", lambda _event: self._draw())
        self.raw_canvas.bind("<ButtonPress-1>", self._begin_crop)
        self.raw_canvas.bind("<B1-Motion>", self._drag_crop)
        self.raw_canvas.bind("<ButtonRelease-1>", self._end_crop)

        footer = ttk.Frame(self)
        footer.grid(row=6, column=0, sticky="ew", pady=(6, 0))
        self._button(footer, "上一张", lambda: self._navigate(-1)).pack(side="left")
        self._button(footer, "下一张", lambda: self._navigate(1)).pack(side="left", padx=6)
        self.position_var = tk.StringVar(value="尚未采集")
        ttk.Label(footer, textvariable=self.position_var).pack(side="left", padx=6)
        self.message_var = tk.StringVar(value="采集后可拖动原图调整裁剪范围；右侧自动预览。")
        ttk.Label(self, textvariable=self.message_var, wraplength=720).grid(row=7, column=0, sticky="ew", pady=(6, 0))
        for variable in self.crop_vars:
            variable.trace_add("write", self._schedule_crop_preview)
        self._poll_id = self.after(100, self._poll)

    def _button(self, parent, text, command):
        widget = ttk.Button(parent, text=text, command=command)
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

    def stop(self):
        self._stop.set()
        self.stop_button.configure(state="disabled")
        if self.busy:
            self.message_var.set("正在停止，已保存的图片会保留；等待当前设备调用完成。")

    def destroy(self):
        self._stop.set()
        if self._crop_preview_id is not None:
            self.after_cancel(self._crop_preview_id)
        self.after_cancel(self._poll_id)
        super().destroy()

    def _schedule_crop_preview(self, *_args):
        if self._crop_preview_id is not None:
            self.after_cancel(self._crop_preview_id)
        self._crop_preview_id = self.after(80, self._refresh_crop_preview)

    def _refresh_crop_preview(self):
        self._crop_preview_id = None
        self._draw()

    def _crop(self):
        try:
            return validate_crop(tuple(int(variable.get()) for variable in self.crop_vars), self._screen_size())
        except ValueError as error:
            width, height = self._screen_size()
            raise ValueError(f"请输入有效裁剪范围：0≤左<右≤{width}，0≤上<下≤{height}") from error

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
        crop = self._crop()
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
            skip_similar = not single and self.skip_similar_var.get()
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
                skip_similar=skip_similar,
            )

        self._start(job)

    def _import_file(self):
        if self.busy or self.running:
            return
        source = filedialog.askopenfilename(
            parent=self,
            title="导入截图或录制视频",
            filetypes=[("截图／视频", "*.png *.jpg *.jpeg *.mkv *.mp4")],
        )
        if source:
            self._import(Path(source))

    def _import_folder(self):
        if self.busy or self.running:
            return
        source = filedialog.askdirectory(parent=self, title="选择图片目录或已解压的单场录制包")
        if source:
            self._import(Path(source))

    def _import(self, source):
        try:
            root, crop, count, interval = self._collection_options()
        except ValueError as error:
            self.message_var.set(str(error))
            return

        def job():
            session = PreparationSession.create(root, crop)
            self._publish(session)
            import_screenshots(
                session, source, count=count, interval=interval, stop=self._stop, progress=self._progress
            )

        self._start(job)

    def _open_session(self):
        if self.busy or self.running:
            return
        source = filedialog.askdirectory(parent=self, title="选择包含 metadata.json 的战场数据批次")
        if source:
            self._start(lambda: self._publish(PreparationSession.open(Path(source))))

    def _recrop(self):
        if self.busy or self.running:
            return
        if self.session is None or not self.session.metadata["frames"]:
            self.message_var.set("请先采集、导入或打开一个批次。")
            return
        try:
            root_text = self.root_var.get().strip()
            if not root_text:
                raise ValueError("请填写保存目录")
            root, crop = Path(root_text).expanduser(), self._crop()
        except ValueError as error:
            self.message_var.set(str(error))
            return
        source_session = self.session

        def job():
            recrop_session(source_session, root, crop, stop=self._stop, progress=self._progress, created=self._publish)

        self._start(job)

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
                    self.frame_image = None
                    self._needs_preview = True
                    self.position_var.set("暂无图片")
                    for variable, coordinate in zip(self.crop_vars, value.metadata["crop_ltrb"], strict=True):
                        variable.set(str(coordinate))
                    self._draw()
                elif kind == "progress":
                    self.message_var.set(f"已保存 {value} 张原图及裁剪图。")
                elif kind == "preview":
                    self.frame_image = value
                    self._draw()
                else:
                    self.busy = False
                    self._update_controls()
                    if self.busy_callback:
                        self.busy_callback()
                    if kind == "error":
                        self.message_var.set(f"处理未完成：{value}。已保存的图片保留。")
                    elif self.session is not None:
                        if not self._preview_job:
                            for variable, coordinate in zip(
                                self.crop_vars, self.session.metadata["crop_ltrb"], strict=True
                            ):
                                variable.set(str(coordinate))
                        count = len(self.session.metadata["frames"])
                        self.position_var.set(f"{self.index + 1 if count else 0} / {count} 张")
                        if not self._preview_job:
                            skipped = f" · 跳过 {value} 张相似截图" if value else ""
                            self.message_var.set(
                                f"{'已停止' if self._stop.is_set() else '已完成'} · 保存 {count} 张{skipped} · {self.session.directory}"
                            )
                    # A completed capture/import needs one off-thread read of its first frame.
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

    def _reset_crop(self):
        size = self._screen_size()
        crop = tuple(
            round(value * size[index % 2] / GAME_SCREEN_SIZE[index % 2])
            for index, value in enumerate(BATTLEFIELD_DATA_REGION_LTRB)
        )
        for variable, value in zip(self.crop_vars, crop, strict=True):
            variable.set(str(value))
        self._draw()

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
        self.crop_canvas.delete("all")
        self._transform = None
        if self.frame_image is None:
            return
        image = Image.fromarray(cv2.cvtColor(self.frame_image, cv2.COLOR_BGR2RGB))
        self._transform = self._draw_image(self.raw_canvas, image)
        try:
            left, top, right, bottom = self._crop()
        except ValueError:
            return
        x, y, scale_x, scale_y = self._transform
        self.raw_canvas.create_rectangle(
            x + left * scale_x,
            y + top * scale_y,
            x + right * scale_x,
            y + bottom * scale_y,
            outline="#44dd88",
            width=2,
        )
        self._draw_image(self.crop_canvas, image.crop((left, top, right, bottom)))

    def _point(self, event):
        if self._transform is None:
            return None
        x, y, scale_x, scale_y = self._transform
        width, height = self._screen_size()
        return min(width, max(0, round((event.x - x) / scale_x))), min(height, max(0, round((event.y - y) / scale_y)))

    def _begin_crop(self, event):
        self._drag_start = None
        if not self.busy and not self.running:
            self._drag_start = self._point(event)

    def _drag_crop(self, event):
        if self._drag_start is None or self.busy or self.running:
            return
        point = self._point(event)
        if point is None:
            return
        x, y = self._drag_start
        left, right = sorted((x, point[0]))
        top, bottom = sorted((y, point[1]))
        if left == right or top == bottom:
            return
        for variable, value in zip(self.crop_vars, (left, top, right, bottom), strict=True):
            variable.set(str(value))
        self._draw()

    def _end_crop(self, event):
        if self._drag_start is None:
            return
        self._drag_crop(event)
        self._drag_start = None
