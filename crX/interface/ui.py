"""Compact tabbed desktop UI; device operations are dispatched by the application."""

from __future__ import annotations

import threading
import tkinter as tk
from queue import Empty, Queue
from tkinter import filedialog, messagebox

import ttkbootstrap as ttk
from ttkbootstrap.widgets import ToolTip

from crX.emulators import EmulatorType, get_available_emulators
from crX.interface.card_review import CardReviewTab
from crX.interface.config import DEFAULTS, JOBS, MAX_FIGHT_LIMIT, THEME_LABELS
from crX.interface.enums import BATTLE_STAT_LABELS, UIField, has_start_ready_job
from crX.interface.failure_review import FailureReviewTab
from crX.interface.widgets import DualRingGauge
from crX.utils.platform import (
    clear_recordings,
    get_recordings_dir,
    recordings_drive_free_bytes,
    recordings_total_bytes_all_locations,
    validate_recordings_path,
)
from crX.utils.versioning import __version__


def no_jobs_popup():
    messagebox.showinfo("crX", "请先启用一种对战模式，或勾选商店每日免费领取。")


class CrXUI(ttk.Window):
    def __init__(self):
        super().__init__(themename="darkly")
        self.title(f"crX {__version__}")
        self.geometry("850x760")
        self.minsize(760, 700)
        self._state = "idle"
        self._storage_busy = False
        self._storage_results = Queue(maxsize=1)
        self._suspend = True
        self.config_callback = None
        self.device_callback = None
        self.open_logs_callback = None
        self.open_recordings_callback = None
        self.variables: dict[str, tk.Variable] = {}
        self.config_widgets = []
        for key, default in DEFAULTS.items():
            variable = tk.BooleanVar(value=default) if isinstance(default, bool) else tk.StringVar(value=str(default))
            self.variables[key] = variable
        # The visible checkbox is positive; the persisted upstream setting is negative.
        self.variables[UIField.DISABLE_WIN_TRACK_TOGGLE].set(not DEFAULTS[UIField.DISABLE_WIN_TRACK_TOGGLE])
        header = ttk.Frame(self, padding=(16, 12))
        header.pack(fill="x")
        ttk.Label(header, text="crX", font=("Segoe UI", 22, "bold")).pack(side="left")
        ttk.Label(header, text="皇室战争 · MuMu / ADB").pack(side="left", padx=18)
        self.main_btn = ttk.Button(header, text="启动", width=14, bootstyle="success")
        self.main_btn.pack(side="right")
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        self.jobs_tab = ttk.Frame(self.notebook, padding=16)
        self.device_tab = ttk.Frame(self.notebook, padding=16)
        self.stats_tab = ttk.Frame(self.notebook, padding=16)
        self.settings_tab = ttk.Frame(self.notebook, padding=16)
        self.card_samples_tab = CardReviewTab(self.notebook, count_callback=self._update_sample_count)
        self.failure_samples_tab = FailureReviewTab(self.notebook, count_callback=self._update_failure_count)
        for tab, title in (
            (self.jobs_tab, "任务"),
            (self.device_tab, "设备"),
            (self.stats_tab, "统计"),
            (self.card_samples_tab, "卡牌样本"),
            (self.failure_samples_tab, "异常样本"),
            (self.settings_tab, "设置"),
        ):
            self.notebook.add(tab, text=title)
        self._create_jobs()
        self._create_devices()
        self._create_stats()
        self._create_settings()
        self.status_var = tk.StringVar(value="待机")
        ttk.Label(self, textvariable=self.status_var, padding=(16, 8), wraplength=800).pack(
            side="bottom", fill="x", before=self.notebook
        )
        self._suspend = False
        for variable in self.variables.values():
            variable.trace_add("write", self._notify)
        self._schedule_storage_update()
        self.after(200, self._poll_storage)

    def _update_sample_count(self, count):
        title = f"卡牌样本 ({count})" if count else "卡牌样本"
        self.notebook.tab(self.card_samples_tab, text=title)

    def _update_failure_count(self, count):
        title = f"异常样本 ({count})" if count else "异常样本"
        self.notebook.tab(self.failure_samples_tab, text=title)

    def _checkbox(self, parent, key, title, tooltip=""):
        widget = ttk.Checkbutton(parent, text=title, variable=self.variables[key])
        widget.pack(anchor="w", pady=7)
        self.config_widgets.append((widget, "normal"))
        if tooltip:
            ToolTip(widget, text=tooltip)
        return widget

    def _create_jobs(self):
        for column in range(2):
            self.jobs_tab.columnconfigure(column, weight=1)
        groups = {
            "Battles": ttk.Labelframe(self.jobs_tab, text="对战任务", padding=14),
            "Options": ttk.Labelframe(self.jobs_tab, text="卡组与选项", padding=14),
        }
        for column, frame in enumerate(groups.values()):
            frame.grid(row=0, column=column, sticky="nsew", padx=5)
        for job in JOBS:
            frame = (
                groups["Battles"]
                if job.key
                in {
                    UIField.CLASSIC_1V1_USER_TOGGLE,
                    UIField.CLASSIC_2V2_USER_TOGGLE,
                    UIField.TROPHY_ROAD_USER_TOGGLE,
                    UIField.SHOP_DAILY_OFFER_USER_TOGGLE,
                }
                else groups["Options"]
            )
            self._checkbox(frame, job.key, job.title, job.tooltip)
            for key, config in (job.extras or {}).items():
                row = ttk.Frame(frame)
                row.pack(fill="x", pady=(0, 6), padx=(18, 0))
                ttk.Label(row, text=config.label).pack(side="left")
                combo = ttk.Combobox(
                    row, textvariable=self.variables[key], values=config.values, width=6, state="readonly"
                )
                combo.pack(side="right", padx=8)
                self.config_widgets.append((combo, "readonly"))
        control = ttk.Labelframe(self.jobs_tab, text="运行控制", padding=12)
        control.grid(row=1, column=0, columnspan=2, sticky="ew", padx=5, pady=(16, 0))
        limit_row = ttk.Frame(control)
        limit_row.pack(fill="x")
        ttk.Label(limit_row, text="执行局数").pack(side="left")
        self.max_fights_spinbox = ttk.Spinbox(
            limit_row,
            textvariable=self.variables[UIField.MAX_FIGHTS],
            from_=0,
            to=MAX_FIGHT_LIMIT,
            increment=1,
            width=8,
        )
        self.max_fights_spinbox.pack(side="left", padx=12)
        self.config_widgets.append((self.max_fights_spinbox, "normal"))
        ttk.Label(limit_row, text="0 表示不限；各模式合计，商店不计入", wraplength=430).pack(side="left")
        self.stop_after_rewards_checkbox = self._checkbox(
            control,
            UIField.STOP_AFTER_REWARDS,
            "奖励领完后停止",
            "在主界面连续确认对战上方的空皇冠栏后停止；执行局数上限仍有效。仅商店任务不受影响。",
        )
        ttk.Label(
            self.jobs_tab,
            text="可启用一种或多种对战模式，程序将依次轮换。\n商店每日免费领取可单独运行，每小时检查一次。",
            wraplength=700,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=16)

    def _entry_row(self, parent, label, key, browse=None):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text=label, width=18).pack(side="left")
        entry = ttk.Entry(row, textvariable=self.variables[key])
        entry.pack(side="left", fill="x", expand=True)
        self.config_widgets.append((entry, "normal"))
        if browse:
            button = ttk.Button(row, text="浏览", command=browse)
            button.pack(side="left", padx=(8, 0))
            self.config_widgets.append((button, "normal"))
        return row

    def _create_devices(self):
        ttk.Label(self.device_tab, text="设备连接方式").pack(anchor="w")
        self.device_backend_labels = {
            backend: "通用 ADB" if backend == EmulatorType.ADB else str(backend)
            for backend in get_available_emulators()
        }
        self.device_backend_var = tk.StringVar()
        self.device_backend_combo = ttk.Combobox(
            self.device_tab,
            textvariable=self.device_backend_var,
            values=list(self.device_backend_labels.values()),
            state="readonly",
            width=24,
        )
        self.device_backend_combo.pack(anchor="w", pady=8)
        self.device_backend_combo.bind("<<ComboboxSelected>>", lambda event: self._select_device_backend())
        self.config_widgets.append((self.device_backend_combo, "readonly"))
        self.device_body = ttk.Frame(self.device_tab)
        self.device_body.pack(fill="x")
        self.mumu_frame = ttk.Labelframe(self.device_body, text="MuMu", padding=12)
        row = ttk.Frame(self.mumu_frame)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="实例编号", width=18).pack(side="left")
        self.instance_combo = ttk.Combobox(
            row, textvariable=self.variables[UIField.MUMU_INSTANCE], width=10, state="readonly"
        )
        self.instance_combo.pack(side="left")
        self.config_widgets.append((self.instance_combo, "readonly"))
        self.instance_names = tk.StringVar(value="点击刷新以查看已有模拟器实例")
        ttk.Label(self.mumu_frame, textvariable=self.instance_names, wraplength=680).pack(anchor="w")
        self.adb_frame = ttk.Labelframe(self.device_body, text="通用 ADB", padding=12)
        self._entry_row(self.adb_frame, "设备序列号／地址", UIField.ADB_SERIAL)
        self.show_device_advanced = tk.BooleanVar(value=False)
        self.device_advanced_toggle = ttk.Checkbutton(
            self.device_body,
            text="高级设置（自动查找失败时使用）",
            variable=self.show_device_advanced,
            command=self._update_device_fields,
        )
        self.device_advanced_toggle.pack(anchor="w", pady=8)
        self.config_widgets.append((self.device_advanced_toggle, "normal"))
        self.device_advanced_frame = ttk.Frame(self.device_body)
        self.mumu_path_row = self._entry_row(
            self.device_advanced_frame,
            "MuMu 安装目录",
            UIField.MUMU_PATH,
            lambda: self._browse(UIField.MUMU_PATH, directory=True),
        )
        self.adb_path_row = self._entry_row(
            self.device_advanced_frame, "ADB 程序路径", UIField.ADB_PATH, lambda: self._browse(UIField.ADB_PATH)
        )
        self.device_help_var = tk.StringVar()
        self.device_help = ttk.Label(
            self.device_body,
            textvariable=self.device_help_var,
            wraplength=720,
        )
        self.device_help.pack(anchor="w", pady=12)
        row = ttk.Frame(self.device_body)
        row.pack(fill="x")
        for title, action in (
            ("刷新设备", "refresh"),
            ("连接／检查", "connect"),
            ("恢复默认分辨率", "reset"),
        ):
            button = ttk.Button(
                row, text=title, command=lambda a=action: self.device_callback(a) if self.device_callback else None
            )
            button.pack(side="left", padx=(0, 8))
            self.config_widgets.append((button, "normal"))
        self.variables[UIField.EMULATOR].trace_add("write", lambda *_: self._update_device_fields())
        self._update_device_fields()

    def _select_device_backend(self):
        selected = self.device_backend_var.get()
        backend = next((key for key, label in self.device_backend_labels.items() if label == selected), None)
        if backend is not None:
            self.variables[UIField.EMULATOR].set(backend)

    def _update_device_fields(self):
        backend = self.variables[UIField.EMULATOR].get()
        self.device_backend_var.set(self.device_backend_labels.get(backend, backend))
        is_mumu = backend == EmulatorType.MUMU
        self.mumu_frame.pack_forget()
        self.adb_frame.pack_forget()
        selected = self.mumu_frame if is_mumu else self.adb_frame
        selected.pack(fill="x", pady=10, before=self.device_advanced_toggle)
        self.mumu_path_row.pack_forget()
        if is_mumu:
            self.mumu_path_row.pack(fill="x", pady=8, before=self.adb_path_row)
        self.device_advanced_frame.pack_forget()
        if self.show_device_advanced.get():
            self.device_advanced_frame.pack(fill="x", pady=8, before=self.device_help)
        help_text = (
            "自动查找 MuMu 安装目录与自带的 ADB，并获取所选实例的连接地址。\n"
            "自动查找失败时，再展开高级设置填写路径。"
            if is_mumu
            else "请先启动模拟器并开启 ADB 调试。只有一个在线设备时，可留空自动选择。\n"
            "有多个在线设备或需主动连接时，请填写目标设备的序列号或地址。"
        )
        self.device_help_var.set(help_text + "\n启动游戏前，程序会将所选设备设为 419×633 分辨率、160 DPI。")

    def _create_stats(self):
        self.gauge = DualRingGauge(self.stats_tab, diameter=100, canvas_bg=self.style.colors.bg)
        self.gauge.grid(row=0, column=0, rowspan=3, padx=(0, 25), sticky="n")
        self.stat_variables = {}
        for index, (key, title) in enumerate(BATTLE_STAT_LABELS.items()):
            row, column = divmod(index, 2)
            box = ttk.Frame(self.stats_tab, padding=6)
            box.grid(row=row, column=column + 1, sticky="ew")
            ttk.Label(box, text=title).pack(anchor="w")
            variable = tk.StringVar(value="0")
            self.stat_variables[key] = variable
            ttk.Label(box, textvariable=variable, font=("Segoe UI", 13, "bold")).pack(anchor="w")
        self.stats_tab.columnconfigure(1, weight=1)
        self.stats_tab.columnconfigure(2, weight=1)
        self.log = tk.Text(self.stats_tab, height=10, state="disabled", wrap="word")
        self.log.grid(row=8, column=0, columnspan=3, sticky="nsew", pady=(16, 0))
        self.stats_tab.rowconfigure(8, weight=1)
        self.last_status = ""

    def _create_settings(self):
        self.theme_labels = {
            name: THEME_LABELS.get(name, f"自定义主题 {index + 1}")
            for index, name in enumerate(self.style.theme_names())
        }
        self.theme_label_var = tk.StringVar()
        self.theme_combo = ttk.Combobox(
            self.settings_tab,
            textvariable=self.theme_label_var,
            values=list(self.theme_labels.values()),
            state="readonly",
            width=24,
        )
        ttk.Label(self.settings_tab, text="界面主题").pack(anchor="w")
        self.theme_combo.pack(anchor="w", pady=(6, 20))
        self.theme_combo.bind("<<ComboboxSelected>>", lambda event: self._select_theme())
        self.config_widgets.append((self.theme_combo, "readonly"))
        self._checkbox(self.settings_tab, UIField.RECORD_FIGHTS_TOGGLE, "录制 1v1／皇室征程训练数据")
        ttk.Label(
            self.settings_tab,
            text="目标帧率为每秒 3 帧，保存无损画面、操作和对战结果；不录制 2v2。",
            wraplength=700,
        ).pack(anchor="w")
        self._entry_row(
            self.settings_tab,
            "录制目录",
            UIField.RECORDING_FOLDER_PATH,
            lambda: self._browse(UIField.RECORDING_FOLDER_PATH, directory=True),
        )
        self.storage_var = tk.StringVar(value="")
        ttk.Label(self.settings_tab, textvariable=self.storage_var, wraplength=700).pack(anchor="w", pady=10)
        row = ttk.Frame(self.settings_tab)
        row.pack(fill="x", pady=12)
        ttk.Button(
            row,
            text="打开录制目录",
            command=lambda: self.open_recordings_callback() if self.open_recordings_callback else None,
        ).pack(side="left", padx=(0, 8))
        self.clear_button = ttk.Button(
            row, text="清理录制文件", bootstyle="danger-outline", command=self._clear_recordings
        )
        self.clear_button.pack(side="left", padx=(0, 8))
        self.config_widgets.append((self.clear_button, "normal"))
        ttk.Button(
            row, text="打开日志目录", command=lambda: self.open_logs_callback() if self.open_logs_callback else None
        ).pack(side="left")
        ttk.Label(
            self.settings_tab,
            text="crX 基于 py-clash-bot 精简开发，原工程与素材的许可和署名保留在 LICENSE 与 NOTICE.md 中。",
            wraplength=700,
        ).pack(anchor="w", pady=20)

    def _browse(self, key, directory=False):
        title = {
            UIField.MUMU_PATH: "选择 MuMu 安装目录",
            UIField.ADB_PATH: "选择 ADB 程序",
            UIField.RECORDING_FOLDER_PATH: "选择录制目录",
        }[key]
        value = (
            filedialog.askdirectory(parent=self, title=title)
            if directory
            else filedialog.askopenfilename(parent=self, title=title)
        )
        if value:
            self.variables[key].set(value)

    def _select_theme(self):
        selected = self.theme_label_var.get()
        name = next((name for name, label in self.theme_labels.items() if label == selected), None)
        if name is not None:
            self.variables[UIField.THEME_NAME].set(name)
            self._apply_theme()

    def _apply_theme(self):
        name = self.variables[UIField.THEME_NAME].get()
        if name in self.style.theme_names():
            self.theme_label_var.set(self.theme_labels[name])
            self.style.theme_use(name)
            colors = self.style.colors
            self.gauge.set_colours(colors.success, colors.danger, colors.fg, canvas_bg=colors.bg)
            self.log.configure(background=colors.inputbg, foreground=colors.inputfg, insertbackground=colors.fg)

    def _notify(self, *_):
        if not self._suspend and self.config_callback:
            self.config_callback(self.get_all_values())

    def get_all_values(self):
        values = {key: variable.get() for key, variable in self.variables.items()}
        values[UIField.DISABLE_WIN_TRACK_TOGGLE] = not values[UIField.DISABLE_WIN_TRACK_TOGGLE]
        return values

    def set_all_values(self, values):
        self._suspend = True
        try:
            for key, value in values.items():
                if key in self.variables:
                    if isinstance(DEFAULTS[key], bool) and not isinstance(value, bool):
                        continue
                    self.variables[key].set(not value if key == UIField.DISABLE_WIN_TRACK_TOGGLE else value)
            self._apply_theme()
        finally:
            self._suspend = False

    def can_start(self):
        return has_start_ready_job(self.get_all_values())

    def get_button_state(self):
        return self._state

    def set_button_state(self, state):
        self._state = state
        self.main_btn.configure(
            text="停止" if state == "running" else "启动", bootstyle="danger" if state == "running" else "success"
        )
        for widget, normal_state in self.config_widgets:
            widget.configure(state="disabled" if state == "running" else normal_state)

    def update_stats(self, stats):
        for key, variable in self.stat_variables.items():
            if key in stats:
                variable.set(str(stats[key]))
        try:
            rate = float(str(stats.get("winrate", "0%")).rstrip("%"))
        except ValueError:
            rate = 0
        self.gauge._draw_dynamic(rate, f"{rate:.1f}%")

    def set_status(self, status):
        self.status_var.set(status)
        if status != self.last_status:
            self.last_status = status
            self.log.configure(state="normal")
            self.log.insert("end", status + "\n")
            if int(self.log.index("end-1c").split(".")[0]) > 300:
                self.log.delete("1.0", "50.0")
            self.log.see("end")
            self.log.configure(state="disabled")

    def _schedule_storage_update(self):
        custom = self.variables[UIField.RECORDING_FOLDER_PATH].get()
        valid, message = validate_recordings_path(custom)
        self.storage_var.set(message)
        if valid and not self._storage_busy:
            self._storage_busy = True

            def measure():
                try:
                    size = recordings_total_bytes_all_locations(custom_path=custom)
                    free = recordings_drive_free_bytes(custom_path=custom)
                    text = f"录制占用：{size / 1024**3:.2f} GiB"
                    if free is not None:
                        text += f" | 磁盘可用：{free / 1024**3:.1f} GiB"
                    self._storage_results.put((custom, text))
                except OSError:
                    self._storage_results.put((custom, "无法统计录制文件占用空间"))

            threading.Thread(target=measure, name="storage-check", daemon=True).start()
        self.after(30000, self._schedule_storage_update)

    def _poll_storage(self):
        try:
            custom, text = self._storage_results.get_nowait()
            self._storage_busy = False
            if custom == self.variables[UIField.RECORDING_FOLDER_PATH].get():
                self.storage_var.set(text)
        except Empty:
            pass
        self.after(200, self._poll_storage)

    def _clear_recordings(self):
        custom = self.variables[UIField.RECORDING_FOLDER_PATH].get()
        if messagebox.askyesno(
            "清理录制文件",
            f"确认删除以下目录中的 crX 录制包和归档文件？\n{get_recordings_dir(custom_path=custom)}",
            parent=self,
        ):
            removed, freed = clear_recordings(custom_path=custom)
            self.storage_var.set(f"已删除 {removed} 个录制包，释放 {freed / 1024**3:.2f} GiB")
