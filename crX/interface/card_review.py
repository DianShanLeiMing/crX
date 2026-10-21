"""Human review tab: all filesystem work returns through a queue to Tk."""

from __future__ import annotations

import threading
import tkinter as tk
from collections import Counter
from datetime import datetime
from queue import Empty, Queue
from tkinter import messagebox

import ttkbootstrap as ttk
from PIL import ImageTk
from ttkbootstrap.widgets.scrolled import ScrolledFrame

from crX.bot.card_data import CARD_NAMES_ZH
from crX.bot.card_samples import get_card_samples_dir
from crX.utils.card_library import clear_filtered_samples, update_library
from crX.utils.card_review import (
    REASON_LABELS,
    STATUS_LABELS,
    library_card_summaries,
    load_previews,
    sample_progress,
    save_reviews,
    scan_grouped_samples,
    validation_reason,
)
from crX.utils.open_folder import open_folder

MODE_LABELS = {"practice": "练习", "1v1": "1v1", "2v2": "2v2"}
STAGE_ACTIONS = {
    "待确认": ("confirm",),
    "补库": ("promote", "revoke"),
    "全部": ("confirm",),
}


def search_card_choices(choices: dict[str, str], query: str) -> list[str]:
    """Match Chinese names or English IDs without requiring adjacent characters."""

    def normalize(value):
        return "".join(char for char in value.casefold() if char.isalnum())

    tokens = [token for part in query.split() if (token := normalize(part))]
    if not tokens or query in choices:
        return sorted(choices)
    matches = []
    for label, card_id in choices.items():
        text = normalize(label)
        if not all(len(token) <= len(text) and _is_subsequence(token, text) for token in tokens):
            continue
        exact = "".join(tokens) in {normalize(label.split(" (", 1)[0]), normalize(card_id)}
        rank = 0 if exact else 1 if all(token in text for token in tokens) else 2
        matches.append((rank, label))
    return [label for _, label in sorted(matches)]


def _is_subsequence(query: str, text: str) -> bool:
    remaining = iter(text)
    return all(char in remaining for char in query)


class CardReviewTab(ttk.Frame):
    def __init__(self, parent, *, root=None, count_callback=None, clear_busy_callback=None):
        super().__init__(parent, padding=12)
        self.root = root if root is not None else get_card_samples_dir()
        self.count_callback = count_callback
        self.clear_busy_callback = clear_busy_callback
        self._running = self.clearing = False
        self.samples = {}
        self.groups = {}
        self.sample_groups = {}
        self.group_choices = {}
        self._selected_keys = ()
        self._selection_cleared = False
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
        self.rowconfigure(2, weight=1)
        self.summary_var = tk.StringVar(value="正在读取卡牌样本……")
        ttk.Label(self, textvariable=self.summary_var).grid(row=0, column=0, sticky="w")
        tools = ttk.Frame(self)
        tools.grid(row=1, column=0, sticky="ew", pady=4)
        filters = ttk.Frame(tools)
        filters.pack(fill="x")
        selection_tools = ttk.Frame(tools)
        selection_tools.pack(fill="x", pady=(4, 0))
        self.filter_var = tk.StringVar(value="待确认")
        self.filter_combo = ttk.Combobox(
            filters,
            textvariable=self.filter_var,
            values=["待确认", "补库", "全部"],
            state="readonly",
            width=8,
        )
        self.filter_combo.pack(side="left")
        self.filter_combo.bind("<<ComboboxSelected>>", lambda _: self._render_list())
        self.group_var = tk.StringVar(value="全部分组")
        self.group_combo = ttk.Combobox(
            filters, textvariable=self.group_var, values=["全部分组"], state="readonly", width=16
        )
        self.group_combo.pack(side="left", padx=(8, 0))
        self.group_combo.bind("<<ComboboxSelected>>", lambda _: self._render_list())
        ttk.Button(filters, text="刷新", command=self.refresh).pack(side="left", padx=8)
        ttk.Button(filters, text="打开样本目录", command=self._open_folder).pack(side="left")
        self.select_group_button = ttk.Button(
            selection_tools, text="选中本组", command=self._select_group, state="disabled"
        )
        self.select_group_button.pack(side="left", padx=(0, 8))
        self.select_all_button = ttk.Button(
            selection_tools, text="全选当前列表", command=self._select_all, state="disabled"
        )
        self.select_all_button.pack(side="left", padx=(0, 8))
        self.clear_selection_button = ttk.Button(
            selection_tools, text="取消选择", command=self._clear_selection, state="disabled"
        )
        self.clear_selection_button.pack(side="left", padx=(0, 8))
        self.clear_button = ttk.Button(
            selection_tools, text="清空选中", bootstyle="danger-outline", command=self._clear_samples, state="disabled"
        )
        self.clear_button.pack(side="left")
        self.review_panes = ttk.Panedwindow(self, orient="vertical")
        self.review_panes.grid(row=2, column=0, rowspan=2, sticky="nsew", pady=(0, 4))
        table = ttk.Frame(self.review_panes)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(1, weight=1)
        self.review_panes.add(table, weight=1)
        self.selection_var = tk.StringVar(value="已选 0 张；Ctrl / Shift 多选，卡名和备注应用于全部选中样本。")
        selection_label = ttk.Label(table, textvariable=self.selection_var)
        selection_label.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        selection_label.bind("<Configure>", lambda event: selection_label.configure(wraplength=max(1, event.width)))
        self.tree = ttk.Treeview(
            table,
            columns=(
                "group",
                "session",
                "reason",
                "status",
                "card",
                "enabled",
                "runtime",
                "pending",
                "validation_reason",
            ),
            show="headings",
            height=3,
            selectmode="extended",
        )
        for column, title, width in (
            ("group", "相似分组", 100),
            ("session", "对局 / 样本", 210),
            ("reason", "采样原因", 115),
            ("status", "处理状态", 100),
            ("validation_reason", "说明", 210),
            ("card", "确认卡名", 200),
            ("enabled", "已启用指纹数", 100),
            ("runtime", "补库命中次数", 100),
            ("pending", "需复核样本数", 100),
        ):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=width, stretch=False)
        for column in ("card", "validation_reason"):
            self.tree.heading(column, anchor="w")
        for column in ("enabled", "runtime", "pending"):
            self.tree.column(column, anchor="center")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=2, column=0, sticky="ew")
        self.tree.configure(xscrollcommand=horizontal.set)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        scroll.grid(row=1, column=1, sticky="ns")
        self.description_var = tk.StringVar(value="选择样本可查看完整说明。")
        self.description_label = ttk.Label(table, textvariable=self.description_var, justify="left", wraplength=500)
        self.description_label.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.description_label.bind(
            "<Configure>", lambda event: self.description_label.configure(wraplength=max(1, event.width))
        )
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.tree.bind("<Control-a>", self._select_all)
        self.details = ttk.Notebook(self.review_panes)
        self.review_panes.add(self.details, weight=2)
        info = ScrolledFrame(self.details, padding=8, height=220)
        frame = ttk.Frame(self.details, padding=4)
        self.details.add(info.container, text="手牌与识别信息")
        self.details.add(frame, text="完整战斗截图")
        self.card_image = ttk.Label(info, text="选择样本查看截图", anchor="center", width=10)
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
        edit.grid(row=4, column=0, sticky="ew", pady=2)
        ttk.Label(edit, text="确认卡名").pack(side="left")
        self.card_choices = {f"{name} ({card_id})": card_id for card_id, name in CARD_NAMES_ZH.items()}
        self.card_var = tk.StringVar()
        self.card_combo = ttk.Combobox(edit, textvariable=self.card_var, values=sorted(self.card_choices), width=32)
        self.card_combo.pack(side="left", padx=8)
        self.card_combo.configure(postcommand=lambda: self._search_cards(opening=True))
        self.card_var.trace_add("write", self._search_cards)
        self.card_combo.bind("<Return>", self._choose_single_card)
        self.card_combo.bind("<Down>", lambda _: self._move_card_suggestion(1))
        self.card_combo.bind("<Up>", lambda _: self._move_card_suggestion(-1))
        self.card_combo.bind("<Escape>", self._hide_card_suggestions)
        self.card_combo.bind("<FocusOut>", lambda _: self._later(60, self._check_card_search_focus))
        self._card_popup_after = None
        self.card_suggestions = ttk.Treeview(self, show="tree", selectmode="browse", takefocus=False, height=6)
        self.card_suggestions.bind("<ButtonRelease-1>", self._pick_card_suggestion)
        self.card_suggestions.bind("<Return>", self._pick_card_suggestion)
        self.card_suggestions.bind("<Escape>", self._hide_card_suggestions)
        self.card_suggestions.bind("<FocusOut>", lambda _: self._later(60, self._check_card_search_focus))
        self._search_toplevel = self.winfo_toplevel()
        self._search_click_binding = self._search_toplevel.bind("<Button-1>", self._card_search_outside_click, add="+")
        self._search_move_binding = self._search_toplevel.bind("<Configure>", self._card_search_parent_moved, add="+")
        ttk.Label(edit, text="输入中文 / 英文关键词自动展开；↑↓ 选择，回车确认\n请区分普通 / 觉醒 / 精英版本").pack(
            side="left"
        )
        notes = ttk.Frame(self)
        notes.grid(row=5, column=0, sticky="ew", pady=2)
        ttk.Label(notes, text="处理备注").pack(side="left")
        self.note_var = tk.StringVar()
        ttk.Entry(notes, textvariable=self.note_var).pack(side="left", fill="x", expand=True, padx=8)
        actions = ttk.Frame(self)
        actions.grid(row=6, column=0, sticky="ew", pady=2)
        self.stage_buttons = {}
        for key, title, callback in (
            ("confirm", "确认", lambda: self._save("confirmed")),
            ("promote", "启用", lambda: self._library_action("promote")),
            ("revoke", "撤销", lambda: self._library_action("revoke")),
        ):
            self.stage_buttons[key] = ttk.Button(
                actions,
                text=title,
                command=callback,
                state="disabled",
            )
        self.stage_help = tk.StringVar()
        ttk.Label(self, textvariable=self.stage_help, wraplength=700).grid(row=7, column=0, sticky="w", pady=2)
        self.message_var = tk.StringVar(value="确认卡名后自动补库；同卡不同外观共同识别。运行命中仅是观察记录。")
        message = ttk.Label(self, textvariable=self.message_var, wraplength=640)
        message.grid(row=8, column=0, sticky="ew", pady=(4, 0))
        message.bind("<Configure>", lambda event: message.configure(wraplength=max(200, event.width - 8)))
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
        return identifier

    def _on_destroy(self, event):
        if event.widget is self:
            self._closed = True
            self._search_toplevel.unbind("<Button-1>", self._search_click_binding)
            self._search_toplevel.unbind("<Configure>", self._search_move_binding)
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
            self._background("scan", lambda: scan_grouped_samples(root, refresh_library=True), self._revision)

    def _open_folder(self):
        root = str(self.root)
        self._background("folder", lambda: open_folder(root))

    def set_running(self, running):
        self._running = running
        self.clear_button.configure(
            state="normal" if self._selected_clear_keys() and not running and not self._action_busy else "disabled"
        )
        self.select_all_button.configure(
            state="normal" if self.tree.get_children() and not self._action_busy else "disabled"
        )
        self.clear_selection_button.configure(
            state="normal" if self.tree.selection() and not self._action_busy else "disabled"
        )

    def _selected_clear_keys(self):
        visible = set(self.tree.get_children())
        selected = [self.samples[key] for key in self.tree.selection() if key in visible and key in self.samples]
        if self.filter_var.get() == "补库":
            cards = {sample.card_id for sample in selected}
            return {
                sample.key
                for sample in self.samples.values()
                if sample.card_id in cards
                and sample.status in ("confirmed", "trial", "covered", "included", "conflict")
            }
        return {sample.key for sample in selected}

    def _clear_samples(self):
        if self._running or self._action_busy:
            return
        keys = self._selected_clear_keys()
        if not keys:
            self.message_var.set("请先选择要清空的记录，也可全选当前列表。")
            return
        targets = [self.samples[key] for key in keys]
        cards = {sample.card_id for sample in targets if sample.card_id}
        active_cards = {sample.card_id for sample in targets if sample_progress(sample)[2] == "当前使用"}
        names = "、".join(sorted(CARD_NAMES_ZH.get(card, card) for card in cards)[:8])
        scope = (
            f"补库汇总中选中的 {len(cards)} 种卡牌，共 {len(keys)} 张样本（包含这些卡牌的后台候选）"
            if self.filter_var.get() == "补库"
            else f"当前列表中选中的 {len(keys)} 张样本"
        )
        details = f"\n卡牌：{names}{'等' if len(cards) > 8 else ''}。" if names else ""
        if active_cards:
            details += f"\n其中 {len(active_cards)} 种卡牌正在使用的补库指纹也会被删除。"
        if (
            not messagebox.askyesno(
                "清空选中项",
                f"确定清空{scope}？{details}\n会删除相关截图、标注、导出文件及关联补库记录，未选中的其他记录保留。此操作无法撤销。",
                parent=self,
            )
            or self._running
            or self._action_busy
        ):
            return
        self._revision += 1
        self._selection_version += 1
        self._action_busy = self.clearing = True
        self._set_action_state()
        if self.clear_busy_callback:
            self.clear_busy_callback(True)
        self._background("clear", lambda: clear_filtered_samples(self.root, keys))

    def _select_all(self, _event=None):
        if not self._action_busy:
            rows = self.tree.get_children()
            self.tree.selection_set(rows)
            if rows and self.tree.focus() not in rows:
                self.tree.focus(rows[0])
            self._select()
        return "break"

    def _clear_selection(self):
        if not self._action_busy:
            self._selection_cleared = True
            self.tree.selection_remove(self.tree.selection())
            self._select()

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
                    samples, errors, groups = result
                    self.samples = {sample.key: sample for sample in samples}
                    self._update_groups(groups)
                    counts = Counter(sample.status for sample in samples)
                    self.summary_var.set(
                        f"待确认 {counts['pending']} 张  |  补库 {len(library_card_summaries(samples))} 种卡牌"
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
            elif kind in ("save", "library", "clear"):
                self._action_busy = False
                if kind == "clear":
                    self.clearing = False
                    if self.clear_busy_callback:
                        self.clear_busy_callback(False)
                    if error is None:
                        result = f"已清空 {result} 张选中样本及关联补库记录。"
                self._set_action_state()
                if error is None:
                    self.message_var.set(result)
                self.refresh()
            if error:
                self.message_var.set(f"操作失败：{error}")
        self._later(100, self._poll)

    def _update_groups(self, groups):
        prior_group = self.group_choices.get(self.group_var.get())
        self.groups = {group: list(keys) for group, keys in groups.items()}
        self.sample_groups = {key: group for group, keys in groups.items() for key in keys}
        self.group_choices = {
            f"相似组 {index:02d}（{len(keys)} 张）": group for index, (group, keys) in enumerate(groups.items(), 1)
        }
        self.group_combo.configure(values=["全部分组", *self.group_choices])
        self.group_var.set(
            next((label for label, group in self.group_choices.items() if group == prior_group), "全部分组")
        )

    def _render_list(self):
        states = {
            "待确认": {"pending"},
            "补库": {"trial", "included", "covered"},
        }
        selected = self.selected.key if self.selected else None
        selection = self.tree.selection()
        group_filter = self.group_choices.get(self.group_var.get())
        summary = self.filter_var.get() == "补库"
        if summary:
            self.group_var.set("全部分组")
        self.group_combo.configure(state="disabled" if summary else "readonly")
        self.tree.configure(
            displaycolumns=("card", "enabled", "runtime", "pending", "validation_reason")
            if summary
            else ("group", "session", "reason", "status", "card", "runtime", "validation_reason")
        )
        self.tree.heading("status", text="处理状态")
        self.tree.heading("card", text="卡牌名称" if summary else "确认卡名")
        if summary:
            visible = library_card_summaries(list(self.samples.values()))
            by_card = {sample.card_id: sample.key for sample in visible}
            selected_cards = {self.samples[key].card_id for key in selection if key in self.samples}
            if self.selected:
                selected = by_card.get(self.selected.card_id)
            selection = tuple(sample.key for sample in visible if sample.card_id in selected_cards)
        else:
            visible = [
                self.samples[key]
                for group, keys in self.groups.items()
                for key in keys
                if (group_filter is None or group == group_filter)
                and self.samples[key].status in states.get(self.filter_var.get(), set(STATUS_LABELS))
            ]
        # Preserve the selection and an unfinished label while background scans refresh.
        current = tuple(self.tree.get_children())
        desired = tuple(sample.key for sample in visible)
        if current != desired:
            self.tree.delete(*current)
        labels = {group: label.removeprefix("相似组 ") for label, group in self.group_choices.items()}
        for sample in visible:
            verification, runtime, _ = sample_progress(sample)
            description = validation_reason(sample)
            if sample.status == "included" and not sample.review.get("library_issue"):
                description = "已启用；支持多外观"
            values = (
                labels[self.sample_groups[sample.key]],
                sample.key,
                REASON_LABELS.get(sample.record.get("reason"), "识别异常"),
                verification,
                CARD_NAMES_ZH.get(sample.card_id, "—"),
                sample.review.get("summary_enabled", "—"),
                str(sample.review.get("summary_matches", 0)) if summary else runtime,
                sample.review.get("summary_pending", "—"),
                description,
            )
            if self.tree.exists(sample.key):
                self.tree.item(sample.key, values=values)
            else:
                self.tree.insert("", "end", iid=sample.key, values=values)
        remaining = tuple(key for key in selection if key in desired)
        if remaining:
            self.tree.selection_set(remaining)
        if self._selection_cleared and not selection:
            self._set_action_state()
            return
        if selected in desired:
            self.selected = self.samples[selected]
            if selected in self.tree.selection():
                self.tree.focus(selected)
            self._describe_sample(self.selected)
            if not self.tree.selection():
                self.tree.selection_set(selected)
            if self.tree.selection() != self._selected_keys:
                self._select()
            self._set_action_state()
        elif desired:
            if not remaining:
                self.tree.selection_set(desired[0])
            self.tree.focus(self.tree.selection()[0])
            self._select()
        else:
            self.selected = None
            self._selected_keys = ()
            self._selection_version += 1
            for widget in (self.card_image, self.hand_image, self.frame_image):
                widget.configure(image="", text="暂无此类样本")
            self._images.clear()
            self._frame_preview = None
            self.detail_var.set("发现未知牌后会自动出现在这里；可切换筛选查看已处理样本。")
            self.description_var.set("暂无此类样本。")
            self.card_var.set("")
            self.note_var.set("")
            self._set_action_state()

    def _select(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            self._selected_keys = ()
            self.description_var.set("选择样本可查看完整说明。")
            self._set_action_state()
            return
        self._selection_cleared = False
        focus = self.tree.focus()
        key = focus if focus in selection else selection[0]
        if key not in self.samples:
            return
        sample = self.samples[key]
        if self.selected and self.selected.key == sample.key and set(selection) == set(self._selected_keys):
            self._selected_keys = selection
            return
        self._selected_keys = selection
        self.selected = sample
        self._selection_version += 1
        card_ids = {self.samples[key].card_id for key in selection}
        card_id = next(iter(card_ids)) if len(card_ids) == 1 else ""
        self.card_var.set(next((label for label, value in self.card_choices.items() if value == card_id), ""))
        self.card_combo.configure(values=sorted(self.card_choices))
        notes = {
            self.samples[key].review.get("note", self.samples[key].review.get("confirmation", "")) for key in selection
        }
        self.note_var.set(next(iter(notes)) if len(notes) == 1 else "")
        self._describe_sample(sample)
        self._images.clear()
        self._frame_preview = None
        for widget in (self.card_image, self.hand_image, self.frame_image):
            widget.configure(image="", text="正在读取图片……")
        self._background("preview", lambda: load_previews(sample), self._selection_version)
        self._set_action_state()

    def _select_group(self):
        if self.selected is None or self._action_busy or self.filter_var.get() == "补库":
            return
        group = self.sample_groups.get(self.selected.key)
        visible = set(self.tree.get_children())
        keys = [key for key in self.groups.get(group, []) if key in visible]
        self.tree.selection_set(keys)
        self._select()

    def _describe_sample(self, sample):
        self.description_var.set(f"说明：{validation_reason(sample)}")
        verification, runtime_text, _ = sample_progress(sample)
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
            f"{captured}  |  {MODE_LABELS.get(record.get('mode'), record.get('mode', '未知模式'))}  |  第 {slot + 1 if type(slot) is int else '—'} 张手牌  |  对局 {record.get('elapsed_s', '?')} 秒\n{REASON_LABELS.get(record.get('reason'), '识别异常')}  ·  {verification}\n候选仅供参考（距离越小越接近，阈值 {record.get('match_threshold', '—')}）：\n{candidates or '无卡名候选，此样本用于手牌可用性诊断'}"
        )
        if sample.review.get("library_id"):
            self.detail_var.set(
                self.detail_var.get()
                + f"\n补库 {sample.review['library_id']} · {validation_reason(sample)}"
                + f"\n运行记录：{runtime_text}；{verification}。"
                + "\n命中次数只统计该指纹参与识别的次数，不代表自动核验卡名正确。"
            )

    def _resize_frame(self):
        if self._frame_preview is None:
            self.frame_image.configure(image="", text="图片缺失或损坏" if self.selected else "暂无此类样本")
            return
        preview = self._frame_preview.copy()
        preview.thumbnail((max(1, self.frame_image.winfo_width() - 12), max(1, self.frame_image.winfo_height() - 12)))
        self._images["frame"] = ImageTk.PhotoImage(preview, master=self)
        self.frame_image.configure(image=self._images["frame"], text="")

    def _search_cards(self, *_trace_args, opening=False):
        if self._closed:
            return
        query = self.card_var.get().strip()
        # A native dropdown selection writes this variable before it finishes closing.
        # Rebuild the full list only when opening again, not during that selection.
        if query in self.card_choices and not opening:
            self._hide_card_suggestions()
            return
        self.card_combo.configure(values=search_card_choices(self.card_choices, query))
        self._hide_card_suggestions()
        if query and not opening and self._card_search_focus() is self.card_combo:
            self._card_popup_after = self._later(80, self._show_card_suggestions)

    def _hide_card_suggestions(self, _event=None):
        if self._card_popup_after is not None:
            self.after_cancel(self._card_popup_after)
            self._after_ids.discard(self._card_popup_after)
            self._card_popup_after = None
        self.card_suggestions.place_forget()
        return "break"

    def _show_card_suggestions(self):
        self._card_popup_after = None
        if self._card_search_focus() is not self.card_combo:
            return
        matches = self.card_combo.cget("values")
        if not matches:
            return
        self.card_suggestions.delete(*self.card_suggestions.get_children())
        for index, label in enumerate(matches):
            self.card_suggestions.insert("", "end", iid=str(index), text=label)
        self.card_suggestions.configure(height=min(6, len(matches)))
        self.card_suggestions.update_idletasks()
        x = self.card_combo.winfo_rootx() - self.winfo_rootx()
        top = self.card_combo.winfo_rooty() - self.winfo_rooty()
        y = top + self.card_combo.winfo_height()
        height = self.card_suggestions.winfo_reqheight()
        if y + height > self.winfo_height():
            y = max(0, top - height)
        self.card_suggestions.place(x=x, y=y, width=self.card_combo.winfo_width(), height=height)
        self.card_suggestions.lift()

    def _move_card_suggestion(self, direction):
        if not self.card_suggestions.winfo_ismapped():
            return None  # Keep the native full dropdown available when no search is open.
        rows = self.card_suggestions.get_children()
        selected = self.card_suggestions.selection()
        index = rows.index(selected[0]) + direction if selected else 0 if direction > 0 else len(rows) - 1
        key = rows[max(0, min(index, len(rows) - 1))]
        self.card_suggestions.selection_set(key)
        self.card_suggestions.see(key)
        return "break"

    def _pick_card_suggestion(self, event=None):
        if event is not None and event.type == tk.EventType.ButtonRelease:
            if not self.card_suggestions.identify_row(event.y):
                return "break"
        selected = self.card_suggestions.selection()
        if selected:
            self.card_var.set(self.card_suggestions.item(selected[0], "text"))
            self._hide_card_suggestions()
            self.card_combo.focus_set()
            self.card_combo.icursor(tk.END)
        return "break"

    def _card_search_focus(self):
        try:
            return self.focus_get()
        except KeyError:
            # Native combobox popdowns are Tcl widgets without Python widget objects.
            return None

    def _check_card_search_focus(self):
        if self._card_search_focus() not in (self.card_combo, self.card_suggestions):
            self._hide_card_suggestions()

    def _card_search_outside_click(self, event):
        if event.widget not in (self.card_combo, self.card_suggestions):
            self._hide_card_suggestions()

    def _card_search_parent_moved(self, event):
        if event.widget is self._search_toplevel:
            self._hide_card_suggestions()

    def _choose_single_card(self, _event=None):
        if self.card_suggestions.winfo_ismapped() and self.card_suggestions.selection():
            return self._pick_card_suggestion()
        query = self.card_var.get().strip()
        exact_ids = [label for label, card_id in self.card_choices.items() if card_id.casefold() == query.casefold()]
        matches = exact_ids or search_card_choices(self.card_choices, query)
        if len(matches) == 1:
            self.card_var.set(matches[0])
            self._hide_card_suggestions()
            self.card_combo.icursor(tk.END)
        return "break"

    def _set_action_state(self):
        self.set_running(self._running)
        count = len(self.tree.selection())
        stage = self.filter_var.get()
        self.stage_buttons["confirm"].configure(text="确认" if stage == "待确认" else "更正卡名")
        self.selection_var.set(
            f"共 {len(self.tree.get_children())} 种卡牌，已选 {count} 种；清空选中会删除所选卡牌的全部候选，包含当前使用项。"
            if stage == "补库"
            else f"已选 {count} 张；Ctrl / Shift 多选，卡名和备注应用于全部选中样本。"
        )
        visible = STAGE_ACTIONS.get(stage, ())
        action_samples = self._action_samples()
        selected_statuses = {sample.status for sample in action_samples}
        eligible = {
            "confirm": {"pending", "confirmed", "trial", "included", "covered", "conflict"},
            "promote": {"confirmed", "trial", "included", "covered", "conflict"},
            "revoke": {"trial", "included", "covered", "conflict"},
        }
        for key, button in self.stage_buttons.items():
            button.pack_forget()
            if key not in visible:
                continue
            button.pack(side="left", padx=(0, 8))
            enabled = bool(selected_statuses.intersection(eligible[key]))
            button.configure(state="normal" if enabled and not self._action_busy else "disabled")
        self.stage_help.set(
            {
                "待确认": "确认卡名后自动补库并启用；清空只处理选中样本，全选只作用于当前列表。",
                "补库": "每卡汇总多份外观指纹；在“全部”中可更正具体样本卡名。撤销停用该卡指纹；清空选中会删除所选卡牌的全部候选，确认框会列明范围。",
            }.get(stage, "可选择具体样本确认或更正卡名，也可多选后清空；未知样本不自动继承预测卡名。")
        )
        self.select_group_button.configure(
            state="normal" if count and not self._action_busy and stage != "补库" else "disabled"
        )

    def _action_samples(self):
        selected = [self.samples[key] for key in self.tree.selection() if key in self.samples]
        if self.filter_var.get() == "补库":
            cards = {sample.card_id for sample in selected}
            return [sample for sample in self.samples.values() if sample.card_id in cards]
        return selected

    def _library_action(self, action):
        allowed = {
            "promote": {"confirmed", "trial", "included", "covered", "conflict"},
            "revoke": {"trial", "included", "covered", "conflict"},
        }[action]
        samples = [sample for sample in self._action_samples() if sample.status in allowed]
        if not samples or self._action_busy:
            return
        self._revision += 1
        self._action_busy = True
        self._set_action_state()
        self.message_var.set("正在处理补库……")

        def apply():
            if action == "revoke":
                return update_library(self.root, samples, action)
            return update_library(self.root, samples, "enable")

        self._background("library", apply)

    def _save(self, status):
        samples = [
            self.samples[key]
            for key in self.tree.selection()
            if key in self.samples and self.samples[key].status != "ignored"
        ]
        if not samples or self._action_busy:
            return
        entered = self.card_var.get().strip()
        card_id = self.card_choices.get(entered, entered)
        if card_id not in CARD_NAMES_ZH:
            card_id = next((key for key, name in CARD_NAMES_ZH.items() if name == entered), card_id)
        if status == "confirmed" and card_id not in CARD_NAMES_ZH:
            self.message_var.set("请搜索并选择名录中的准确卡名；名录尚未包含的卡牌请保留待确认并填写备注。")
            return
        note = self.note_var.get()
        self._revision += 1
        self._action_busy = True
        self._set_action_state()
        self.message_var.set("正在确认卡名并补库……")

        def confirm_and_validate():
            count = save_reviews(samples, status, card_id, note)
            try:
                candidate_result = update_library(self.root, samples, "enable")
            except (OSError, ValueError, TypeError) as error:
                raise ValueError(
                    f"已保存 {count} 张样本的卡名；补库失败，可在“补库”中点击“启用”重试：{error}"
                ) from error
            return f"已确认 {count} 张样本；{candidate_result}。启用数量及冲突提示见“补库”。"

        self._background("save", confirm_and_validate)
