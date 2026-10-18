"""Human review tab: all filesystem work returns through a queue to Tk."""

from __future__ import annotations

import threading
import tkinter as tk
from collections import Counter
from datetime import datetime
from queue import Empty, Queue

import ttkbootstrap as ttk
from PIL import ImageTk
from ttkbootstrap.widgets.scrolled import ScrolledFrame

from crX.bot.card_data import CARD_NAMES_ZH
from crX.bot.card_samples import get_card_samples_dir
from crX.utils.card_review import (
    REASON_LABELS,
    STATUS_LABELS,
    export_candidate,
    load_previews,
    save_review,
    scan_samples,
)
from crX.utils.open_folder import open_folder

MODE_LABELS = {"practice": "练习", "1v1": "1v1", "2v2": "2v2"}


class CardReviewTab(ttk.Frame):
    def __init__(self, parent, *, root=None, count_callback=None):
        super().__init__(parent, padding=12)
        self.root = root if root is not None else get_card_samples_dir()
        self.count_callback = count_callback
        self.samples = {}
        self.selected = None
        self._results = Queue()
        self._scan_busy = False
        self._action_busy = False
        self._images = {}
        self._frame_preview = None
        self._selection_version = 0
        self._revision = 0
        self._closed = False
        self._after_ids = set()
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self.summary_var = tk.StringVar(value="正在读取卡牌样本……")
        ttk.Label(self, textvariable=self.summary_var).grid(row=0, column=0, sticky="w")
        tools = ttk.Frame(self)
        tools.grid(row=1, column=0, sticky="ew", pady=8)
        self.filter_var = tk.StringVar(value="待确认")
        self.filter_combo = ttk.Combobox(
            tools,
            textvariable=self.filter_var,
            values=["待确认", "已确认", "已补库", "已忽略", "全部"],
            state="readonly",
            width=12,
        )
        self.filter_combo.pack(side="left")
        self.filter_combo.bind("<<ComboboxSelected>>", lambda _: self._render_list())
        ttk.Button(tools, text="刷新", command=self.refresh).pack(side="left", padx=8)
        ttk.Button(tools, text="打开样本目录", command=self._open_folder).pack(side="left")
        table = ttk.Frame(self)
        table.grid(row=2, column=0, sticky="ew")
        self.tree = ttk.Treeview(
            table, columns=("session", "reason", "status", "card"), show="headings", height=5, selectmode="browse"
        )
        for column, title, width in (
            ("session", "对局 / 样本", 220),
            ("reason", "采样原因", 125),
            ("status", "处理状态", 140),
            ("card", "确认卡名", 160),
        ):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=65)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="x", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.details = ttk.Notebook(self)
        self.details.grid(row=3, column=0, sticky="nsew", pady=(10, 6))
        info = ScrolledFrame(self.details, padding=8, height=220)
        frame = ttk.Frame(self.details, padding=4)
        self.details.add(info.container, text="手牌与识别信息")
        self.details.add(frame, text="完整战斗截图")
        self.card_image = ttk.Label(info, text="选择一条样本查看截图", anchor="center", width=17)
        self.card_image.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 12))
        self.detail_var = tk.StringVar(value="暂无待确认样本时，对战会正常继续。")
        ttk.Label(info, textvariable=self.detail_var, wraplength=520, justify="left").grid(row=0, column=1, sticky="nw")
        self.hand_image = ttk.Label(info, anchor="w")
        self.hand_image.grid(row=1, column=1, sticky="sw", pady=(8, 0))
        info.columnconfigure(1, weight=1)
        self.frame_image = ttk.Label(frame, anchor="center")
        self.frame_image.pack(fill="both", expand=True)
        self.frame_image.bind("<Configure>", lambda _: self._resize_frame())
        edit = ttk.Frame(self)
        edit.grid(row=4, column=0, sticky="ew", pady=4)
        ttk.Label(edit, text="确认卡名").pack(side="left")
        self.card_choices = {f"{name} ({card_id})": card_id for card_id, name in CARD_NAMES_ZH.items()}
        self.card_var = tk.StringVar()
        self.card_combo = ttk.Combobox(edit, textvariable=self.card_var, values=sorted(self.card_choices), width=32)
        self.card_combo.pack(side="left", padx=8)
        self.card_combo.bind("<KeyRelease>", self._search_cards)
        ttk.Label(edit, text="区分进化 / 英雄版本").pack(side="left")
        notes = ttk.Frame(self)
        notes.grid(row=5, column=0, sticky="ew", pady=4)
        ttk.Label(notes, text="处理备注").pack(side="left")
        self.note_var = tk.StringVar()
        ttk.Entry(notes, textvariable=self.note_var).pack(side="left", fill="x", expand=True, padx=8)
        actions = ttk.Frame(self)
        actions.grid(row=6, column=0, sticky="ew", pady=4)
        self.action_buttons = []
        for title, callback in (
            ("确认卡名并保存", lambda: self._save("confirmed")),
            ("忽略此样本", lambda: self._save("ignored")),
            ("恢复待确认", lambda: self._save("pending")),
            ("导出补库候选", self._export),
        ):
            button = ttk.Button(actions, text=title, command=callback, state="disabled")
            button.pack(side="left", padx=(0, 8))
            self.action_buttons.append(button)
        self.message_var = tk.StringVar(
            value="自动刷新；查看和标注不会暂停对战。确认卡名后仍需补库验证，导出不会直接生效。"
        )
        ttk.Label(self, textvariable=self.message_var, wraplength=740).grid(row=7, column=0, sticky="w", pady=(4, 0))
        self._later(100, self._poll)
        self._later(5000, self._auto_refresh)
        self.refresh()

    def _later(self, delay, callback):
        def run():
            self._after_ids.discard(identifier)
            if not self._closed:
                callback()

        identifier = self.after(delay, run)
        self._after_ids.add(identifier)

    def _on_destroy(self, event):
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

        threading.Thread(target=run, name=f"card-review-{kind}", daemon=True).start()

    def refresh(self):
        if not self._scan_busy and not self._action_busy:
            self._scan_busy = True
            root = self.root
            self._background("scan", lambda: scan_samples(root), self._revision)

    def _open_folder(self):
        root = str(self.root)
        self._background("folder", lambda: open_folder(root))

    def _auto_refresh(self):
        self.refresh()
        self._later(5000, self._auto_refresh)

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
                    samples, errors = result
                    self.samples = {sample.key: sample for sample in samples}
                    counts = Counter(sample.status for sample in samples)
                    self.summary_var.set(
                        f"待确认 {counts['pending']}  |  已确认待补库 {counts['confirmed']}  |  已补库 {counts['included']}  |  已忽略 {counts['ignored']}"
                    )
                    if self.count_callback:
                        self.count_callback(counts["pending"])
                    self._render_list()
                    if errors:
                        self.message_var.set(f"有 {errors} 条不完整或无效记录已跳过，可在写入完成后刷新。")
            elif kind == "preview" and token == self._selection_version:
                if error is None:
                    for image_kind, widget in (
                        ("card", self.card_image),
                        ("hand", self.hand_image),
                        ("frame", self.frame_image),
                    ):
                        preview = result.get(image_kind)
                        if image_kind == "frame":
                            self._frame_preview = preview
                            self._resize_frame()
                            continue
                        photo = ImageTk.PhotoImage(preview, master=self) if preview is not None else None
                        self._images[image_kind] = photo
                        widget.configure(image=photo or "", text="图片缺失或损坏" if photo is None else "")
            elif kind in ("save", "export"):
                self._action_busy = False
                self._set_action_state()
                if error is None:
                    self.message_var.set(
                        f"补库候选已导出：{result}；验证后再加入识别库。"
                        if kind == "export"
                        else "标注已保存；对战继续按配置执行。确认卡名不等于已补库。"
                    )
                    self.refresh()
            if error:
                self.message_var.set(f"操作失败：{error}")
        self._later(100, self._poll)

    def _render_list(self):
        states = {"待确认": {"pending"}, "已确认": {"confirmed"}, "已补库": {"included"}, "已忽略": {"ignored"}}
        selected = self.selected.key if self.selected else None
        visible = [
            sample
            for sample in self.samples.values()
            if sample.status in states.get(self.filter_var.get(), set(STATUS_LABELS))
        ]
        # Preserve the selection and an unfinished label while background scans refresh.
        current = tuple(self.tree.get_children())
        desired = tuple(sample.key for sample in visible)
        if current != desired:
            self.tree.delete(*current)
        for sample in visible:
            values = (
                sample.key,
                REASON_LABELS.get(sample.record.get("reason"), "识别异常"),
                STATUS_LABELS[sample.status],
                CARD_NAMES_ZH.get(sample.card_id, "—"),
            )
            if self.tree.exists(sample.key):
                self.tree.item(sample.key, values=values)
            else:
                self.tree.insert("", "end", iid=sample.key, values=values)
        if selected in desired:
            self.selected = self.samples[selected]
            self._describe_sample(self.selected)
            if not self.tree.selection():
                self.tree.selection_set(selected)
            self._set_action_state()
        elif desired:
            self.tree.selection_set(desired[0])
            self._select()
        else:
            self.selected = None
            self._selection_version += 1
            for widget in (self.card_image, self.hand_image, self.frame_image):
                widget.configure(image="", text="暂无此类样本")
            self._images.clear()
            self._frame_preview = None
            self.detail_var.set("发现未知牌后会自动出现在这里；可切换筛选查看已处理样本。")
            self.card_var.set("")
            self.note_var.set("")
            self._set_action_state()

    def _select(self, _event=None):
        selection = self.tree.selection()
        if not selection or selection[0] not in self.samples:
            return
        sample = self.samples[selection[0]]
        if self.selected and self.selected.key == sample.key:
            return
        self.selected = sample
        self._selection_version += 1
        self.card_var.set(
            next((label for label, card_id in self.card_choices.items() if card_id == sample.card_id), "")
        )
        self.card_combo.configure(values=sorted(self.card_choices))
        self.note_var.set(sample.review.get("note", sample.review.get("confirmation", "")))
        self._describe_sample(sample)
        self._images.clear()
        self._frame_preview = None
        for widget in (self.card_image, self.hand_image, self.frame_image):
            widget.configure(image="", text="正在读取图片……")
        self._background("preview", lambda: load_previews(sample), self._selection_version)
        self._set_action_state()

    def _describe_sample(self, sample):
        record = sample.record
        candidates = "\n".join(
            f"{index}. {CARD_NAMES_ZH.get(candidate.get('card_id'), candidate.get('card_id', '?'))}  距离 {candidate.get('distance', '?')}"
            for index, candidate in enumerate(record.get("candidates", []), 1)
        )
        try:
            captured = datetime.fromtimestamp(record.get("captured_at", 0)).strftime("%m-%d %H:%M:%S")
        except (ValueError, OverflowError, OSError, TypeError):
            captured = "未知时间"
        slot = record.get("slot")
        self.detail_var.set(
            f"{captured}  |  {MODE_LABELS.get(record.get('mode'), record.get('mode', '未知模式'))}  |  第 {slot + 1 if type(slot) is int else '—'} 张手牌  |  对局 {record.get('elapsed_s', '?')} 秒\n{REASON_LABELS.get(record.get('reason'), '识别异常')}  ·  {STATUS_LABELS[sample.status]}\n候选仅供参考（距离越小越接近，阈值 {record.get('match_threshold', '—')}）：\n{candidates or '无卡名候选，此样本用于手牌可用性诊断'}"
        )

    def _resize_frame(self):
        if self._frame_preview is None:
            self.frame_image.configure(image="", text="图片缺失或损坏" if self.selected else "暂无此类样本")
            return
        preview = self._frame_preview.copy()
        preview.thumbnail((max(1, self.frame_image.winfo_width() - 12), max(1, self.frame_image.winfo_height() - 12)))
        self._images["frame"] = ImageTk.PhotoImage(preview, master=self)
        self.frame_image.configure(image=self._images["frame"], text="")

    def _search_cards(self, _event=None):
        query = self.card_var.get().strip().lower()
        self.card_combo.configure(values=[label for label in sorted(self.card_choices) if query in label.lower()])

    def _set_action_state(self):
        for button in self.action_buttons:
            button.configure(state="normal" if self.selected and not self._action_busy else "disabled")

    def _save(self, status):
        if self.selected is None or self._action_busy:
            return
        entered = self.card_var.get().strip()
        card_id = self.card_choices.get(entered, entered)
        if card_id not in CARD_NAMES_ZH:
            card_id = next((key for key, name in CARD_NAMES_ZH.items() if name == entered), card_id)
        if status == "confirmed" and card_id not in CARD_NAMES_ZH:
            self.message_var.set("请搜索并选择名录中的准确卡名；名录尚未包含的卡牌请保留待确认并填写备注。")
            return
        sample, note = self.selected, self.note_var.get()
        self._revision += 1
        self._action_busy = True
        self._set_action_state()
        self._background("save", lambda: save_review(sample, status, card_id, note))

    def _export(self):
        if self.selected is None or self._action_busy:
            return
        sample = self.selected
        if sample.status not in ("confirmed", "included"):
            self.message_var.set("请先确认卡名并保存，再导出补库候选。")
            return
        self._action_busy = True
        self._set_action_state()
        self._background("export", lambda: export_candidate(sample))
