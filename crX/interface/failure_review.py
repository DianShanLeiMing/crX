"""Visual review of recovery evidence; no game controls or recognition updates."""

from __future__ import annotations

import json
import threading
import tkinter as tk
from collections import Counter
from datetime import datetime
from queue import Empty, Queue
from tkinter import messagebox

import ttkbootstrap as ttk
from PIL import ImageTk
from ttkbootstrap.widgets.scrolled import ScrolledFrame

from crX.utils.failure_samples import (
    STATUS_LABELS,
    delete_failure_samples,
    get_failure_samples_dir,
    load_failure_preview,
    save_failure_review,
    scan_failures,
)
from crX.utils.open_folder import open_folder

STEP_LABELS = {
    "start": "初始化",
    "restart": "恢复游戏",
    "shop_daily": "商店领取",
    "select_battle_mode": "选择模式",
    "randomize_deck": "随机卡组",
    "cycle_deck": "轮换卡组",
    "start_fight": "开始对战",
    "fight": "战斗",
    "end_fight": "战后返回",
}


class FailureReviewTab(ttk.Frame):
    def __init__(self, parent, *, root=None, count_callback=None, clear_busy_callback=None):
        super().__init__(parent, padding=12)
        self.root = root if root is not None else get_failure_samples_dir()
        self.count_callback = count_callback
        self.clear_busy_callback = clear_busy_callback
        self._running = self.clearing = self._selection_cleared = False
        self.records = {}
        self.selected = None
        self._results = Queue()
        self._busy = self._scan_busy = self._closed = False
        self._version = self._revision = 0
        self._preview = self._photo = None
        self._after_ids = set()
        self.bind("<Destroy>", self._destroyed, add="+")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.summary_var = tk.StringVar(value="正在读取异常流程……")
        ttk.Label(self, textvariable=self.summary_var).grid(row=0, column=0, sticky="w")
        toolbar = ttk.Frame(self)
        toolbar.grid(row=1, column=0, sticky="ew", pady=8)
        filters = ttk.Frame(toolbar)
        filters.pack(fill="x")
        self.filter_var = tk.StringVar(value="待处理")
        combo = ttk.Combobox(
            filters, textvariable=self.filter_var, values=[*STATUS_LABELS.values(), "全部"], state="readonly", width=12
        )
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda _: self._render())
        ttk.Button(filters, text="刷新", command=self.refresh).pack(side="left", padx=8)
        ttk.Button(filters, text="打开样本目录", command=self._open_folder).pack(side="left")
        selection_tools = ttk.Frame(toolbar)
        selection_tools.pack(fill="x", pady=(4, 0))
        self.select_all_button = ttk.Button(selection_tools, text="全选当前列表", command=self._select_all)
        self.select_all_button.pack(side="left", padx=(0, 8))
        self.clear_selection_button = ttk.Button(selection_tools, text="取消选择", command=self._clear_selection)
        self.clear_selection_button.pack(side="left", padx=(0, 8))
        self.delete_button = ttk.Button(
            selection_tools,
            text="删除选中",
            command=self._delete_selected,
            bootstyle="danger-outline",
            state="disabled",
        )
        self.delete_button.pack(side="left")
        self.review_panes = ttk.Panedwindow(self, orient="vertical")
        self.review_panes.grid(row=2, column=0, rowspan=2, sticky="nsew", pady=(0, 10))
        table = ttk.Frame(self.review_panes)
        self.review_panes.add(table, weight=1)
        self.selection_var = tk.StringVar(value="已选 0 条；Ctrl / Shift 多选，全选只作用于当前列表。")
        selection_label = ttk.Label(table, textvariable=self.selection_var, wraplength=600)
        selection_label.pack(fill="x", pady=(0, 4))
        selection_label.bind("<Configure>", lambda event: selection_label.configure(wraplength=max(1, event.width)))
        rows = ttk.Frame(table)
        rows.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            rows, columns=("time", "step", "reason", "status"), show="headings", height=3, selectmode="extended"
        )
        for column, title, width in (
            ("time", "时间", 145),
            ("step", "失败步骤", 100),
            ("reason", "失败原因", 330),
            ("status", "状态", 90),
        ):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=65)
        scroll = ttk.Scrollbar(rows, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.tree.bind("<Control-a>", self._select_all)
        self.details = ttk.Notebook(self.review_panes)
        self.review_panes.add(self.details, weight=2)
        picture = ttk.Frame(self.details)
        info = ScrolledFrame(self.details, padding=8, height=240)
        self.details.add(picture, text="失败截图")
        self.details.add(info.container, text="失败信息与日志位置")
        self.image_label = ttk.Label(picture, text="暂无待处理异常", anchor="center")
        self.image_label.pack(fill="both", expand=True)
        self.image_label.bind("<Configure>", lambda _: self._draw_preview())
        self.image_label.bind("<Double-Button-1>", lambda _: self._zoom())
        self.info_var = tk.StringVar()
        ttk.Label(info, textvariable=self.info_var, wraplength=700, justify="left").pack(anchor="w", fill="x")
        actions = ttk.Frame(self)
        actions.grid(row=4, column=0, sticky="ew", pady=6)
        self.buttons = []
        for title, status in (
            ("标记已处理", "handled"),
            ("忽略", "ignored"),
            ("恢复待处理", "pending"),
        ):
            button = ttk.Button(actions, text=title, command=lambda s=status: self._save(s), state="disabled")
            button.pack(side="left", padx=(0, 8))
            self.buttons.append(button)
        self.zoom_button = ttk.Button(actions, text="放大截图", command=self._zoom, state="disabled")
        self.zoom_button.pack(side="left", padx=(0, 8))
        self.message_var = tk.StringVar(value="恢复前自动留样；人工标记不影响任务运行，也不会自动修改识别规则。")
        ttk.Label(self, textvariable=self.message_var, wraplength=730).grid(row=5, column=0, sticky="w", pady=4)
        self.refresh()
        self._later(100, self._poll)
        self._later(5000, self._auto_refresh)

    def _later(self, delay, callback):
        def run():
            self._after_ids.discard(identifier)
            if not self._closed:
                callback()

        identifier = self.after(delay, run)
        self._after_ids.add(identifier)

    def _destroyed(self, event):
        if event.widget is self:
            self._closed = True
            for identifier in self._after_ids:
                self.after_cancel(identifier)
            self._after_ids.clear()

    def _background(self, kind, operation, token=None):
        results = self._results

        def run():
            try:
                results.put((kind, token, operation(), None))
            except Exception as error:
                results.put((kind, token, None, str(error)))

        threading.Thread(target=run, name=f"failure-review-{kind}", daemon=True).start()

    def refresh(self):
        if not self._scan_busy and not self._busy:
            self._scan_busy = True
            root = self.root
            self._background("scan", lambda: scan_failures(root), self._revision)

    def _auto_refresh(self):
        self.refresh()
        self._later(5000, self._auto_refresh)

    def _open_folder(self):
        directory = str(self.selected["directory"] if self.selected else self.root)
        self._background("folder", lambda: open_folder(directory))

    def _poll(self):
        while True:
            try:
                kind, token, result, error = self._results.get_nowait()
            except Empty:
                break
            if kind == "scan":
                self._scan_busy = False
                if token != self._revision:
                    self.refresh()
                    continue
                if error is None:
                    records, errors = result
                    self.records = {record["key"]: record for record in records}
                    counts = Counter(record["review"].get("status", "pending") for record in records)
                    self.summary_var.set(
                        "  |  ".join(f"{label} {counts[status]}" for status, label in STATUS_LABELS.items())
                    )
                    if self.count_callback:
                        self.count_callback(counts["pending"])
                    self._render()
                    if errors:
                        self.message_var.set(f"有 {errors} 条记录无法读取；完整记录仍可查看。")
            elif kind == "preview" and token == self._version and error is None:
                self._preview = result
                self._draw_preview()
                self._set_buttons()
            elif kind == "save":
                self._busy = False
                self._set_buttons()
                if error is None:
                    self.message_var.set("人工处理状态已保存；原始截图与失败信息保留。")
                    self.refresh()
            elif kind == "delete":
                self._busy = self.clearing = False
                if self.clear_busy_callback:
                    self.clear_busy_callback(False)
                self._set_buttons()
                if error is None:
                    self.message_var.set(f"已删除 {result} 条异常流程及其截图、诊断信息和处理标记；运行日志保留。")
                self.refresh()
            if error:
                self.message_var.set(f"操作失败：{error}")
        self._later(100, self._poll)

    def _render(self):
        selection = self.tree.selection()
        visible = [
            record
            for record in self.records.values()
            if self.filter_var.get() in ("全部", STATUS_LABELS[record["review"].get("status", "pending")])
        ]
        keys = [record["key"] for record in visible]
        old_key = self.selected["key"] if self.selected else None
        if tuple(self.tree.get_children()) != tuple(keys):
            self.tree.delete(*self.tree.get_children())
        for record in visible:
            data = record["data"]
            values = (
                record["key"][:15],
                STEP_LABELS.get(data["state"], data["state"]),
                data["reason"].replace("\n", " ")[:100],
                STATUS_LABELS[record["review"].get("status", "pending")],
            )
            if self.tree.exists(record["key"]):
                self.tree.item(record["key"], values=values)
            else:
                self.tree.insert("", "end", iid=record["key"], values=values)
        remaining = tuple(key for key in selection if key in keys)
        if not remaining and keys and not self._selection_cleared:
            remaining = (keys[0],)
        self.tree.selection_set(remaining)
        if remaining:
            self.tree.focus(old_key if old_key in remaining else remaining[0])
        self._select()

    def _select(self, _event=None):
        selected = self.tree.selection()
        self._set_buttons()
        if not selected or selected[0] not in self.records:
            self.selected = None
            self._version += 1
            self._preview = self._photo = None
            self.image_label.configure(
                image="", text="选择记录查看截图" if self.tree.get_children() else "暂无此类异常流程"
            )
            self.info_var.set("")
            self._set_buttons()
            return
        self._selection_cleared = False
        key = self.tree.focus() if self.tree.focus() in selected else selected[0]
        record = self.records[key]
        if self.selected and self.selected["key"] == record["key"]:
            return
        self.selected = record
        data = record["data"]
        try:
            captured = datetime.fromtimestamp(data["captured_at"]).strftime("%Y-%m-%d %H:%M:%S")
        except (KeyError, TypeError, ValueError, OSError, OverflowError):
            captured = record["key"]
        self.info_var.set(
            f"采集时间：{captured}\n失败步骤：{STEP_LABELS.get(data['state'], data['state'])}\n失败原因：{data['reason']}\n恢复序号：{data.get('recovery_attempt', '—')}（达到上限时停止）\n状态：{data.get('status_at_failure', '—')}\n诊断详情：{json.dumps(data.get('details', {}), ensure_ascii=False)}\n运行信息：{json.dumps(data.get('context', {}), ensure_ascii=False)}\n截图来源：{data.get('capture_source', '—')}\n截图问题：{data.get('capture_error') or '无'}\n对应日志：{data.get('session_log_path') or '未提供'}\n异常堆栈：\n{data.get('exception') or '无'}"
        )
        self._version += 1
        self._preview = self._photo = None
        self.image_label.configure(image="", text="正在读取失败截图……")
        self._background("preview", lambda: load_failure_preview(record), self._version)
        self._set_buttons()

    def _draw_preview(self):
        if self._preview is None:
            error = self.selected["data"].get("capture_error") if self.selected else None
            self.image_label.configure(image="", text=error or "无截图或图片已损坏，请查看失败信息")
            return
        preview = self._preview.copy()
        preview.thumbnail((max(1, self.image_label.winfo_width() - 16), max(1, self.image_label.winfo_height() - 16)))
        self._photo = ImageTk.PhotoImage(preview, master=self)
        self.image_label.configure(image=self._photo, text="")

    def _set_buttons(self):
        count = len(self.tree.selection())
        self.selection_var.set(f"已选 {count} 条；Ctrl / Shift 多选，全选只作用于当前列表；状态标记请单选。")
        for button in self.buttons:
            button.configure(state="normal" if count == 1 and not self._busy else "disabled")
        self.zoom_button.configure(state="normal" if self._preview is not None else "disabled")
        self.select_all_button.configure(state="normal" if self.tree.get_children() and not self._busy else "disabled")
        self.clear_selection_button.configure(state="normal" if count and not self._busy else "disabled")
        self.delete_button.configure(state="normal" if count and not self._busy and not self._running else "disabled")

    def set_running(self, running):
        self._running = running
        self._set_buttons()

    def _select_all(self, _event=None):
        if not self._busy:
            keys = self.tree.get_children()
            self.tree.selection_set(keys)
            if keys and self.tree.focus() not in keys:
                self.tree.focus(keys[0])
            self._select()
        return "break"

    def _clear_selection(self):
        if not self._busy:
            self._selection_cleared = True
            self.tree.selection_remove(*self.tree.selection())
            self._select()

    def _delete_selected(self):
        if self._running or self._busy:
            return
        keys = set(self.tree.selection()).intersection(self.tree.get_children(), self.records)
        if not keys:
            return
        if not messagebox.askyesno(
            "删除选中异常流程",
            f"确定删除“{self.filter_var.get()}”列表中选中的 {len(keys)} 条异常流程？\n"
            "将删除这些记录的截图、诊断信息和处理标记；未选中记录及运行日志保留。此操作无法撤销。",
            parent=self,
            default=messagebox.NO,
        ):
            return
        if self._running or self._busy:
            return
        self._revision += 1
        self._version += 1
        self._busy = self.clearing = True
        self._set_buttons()
        if self.clear_busy_callback:
            self.clear_busy_callback(True)
        self.message_var.set(f"正在删除选中的 {len(keys)} 条异常流程……")
        root = self.root
        self._background("delete", lambda: delete_failure_samples(root, keys))

    def _zoom(self):
        if self._preview is None:
            return
        original = self._preview.copy()
        window = tk.Toplevel(self)
        window.title("失败截图 · 可调整窗口大小")
        window.geometry(
            f"{min(original.width + 40, self.winfo_screenwidth() - 80)}x{min(original.height + 40, self.winfo_screenheight() - 100)}"
        )
        label = ttk.Label(window, anchor="center")
        label.pack(fill="both", expand=True)
        photos = {}

        def resize(_event=None):
            preview = original.copy()
            preview.thumbnail((max(1, label.winfo_width() - 12), max(1, label.winfo_height() - 12)))
            photos["image"] = ImageTk.PhotoImage(preview, master=window)
            label.configure(image=photos["image"])

        label.bind("<Configure>", resize)

    def _save(self, status):
        if not self.selected or len(self.tree.selection()) != 1 or self._busy:
            return
        record = self.selected
        self._busy = True
        self._revision += 1
        self._set_buttons()
        self._background("save", lambda: save_failure_review(record, status, record["review"].get("note", "")))
