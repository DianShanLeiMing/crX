"""Human review tab: all filesystem work returns through a queue to Tk."""

from __future__ import annotations

import threading
import tkinter as tk
from collections import Counter, OrderedDict
from datetime import datetime
from queue import Empty, Queue
from tkinter import messagebox
from tkinter.font import Font

import ttkbootstrap as ttk
from PIL import ImageTk
from ttkbootstrap.widgets.scrolled import ScrolledFrame

from crX.bot.card_data import CARD_NAMES_ZH
from crX.bot.card_samples import get_card_samples_dir
from crX.interface.card_catalog import CardCatalogView
from crX.utils.card_catalog import build_catalog
from crX.utils.card_review import (
    REASON_LABELS,
    ReviewSample,
    load_card_thumbnail,
    load_previews,
    sample_progress,
    validation_reason,
)
from crX.utils.card_upload import upload_references
from crX.utils.card_workflow import (
    REVIEW_TABS,
    change_result,
    confirm_samples,
    delete_samples,
    edit_formal_reference,
    reference_details,
    scan_review,
)
from crX.utils.open_folder import open_folder

MODE_LABELS = {"practice": "练习", "1v1": "1v1", "2v2": "2v2"}
STAGE_ACTIONS = {
    "待确认": ("confirm", "ignore"),
    "待上传": ("rename", "remove"),
    "样本库": (),
    "已忽略": ("restore",),
}
COLUMN_SPECS = {
    "group": ("相似分组", 100),
    "card": ("卡名 / 候选提示", 180),
    "reason": ("采样原因", 110),
    "status": ("处理结果", 80),
    "validation_reason": ("说明", 250),
    "references": ("正式参考", 100),
}
FILTER_COLUMNS = {
    "待确认": ("group", "card", "reason", "status"),
    "待上传": ("card", "references", "status"),
    "已忽略": ("card", "status", "validation_reason"),
}
THUMBNAIL_CACHE_SIZE = 128
REVIEW_THUMBNAIL_SIZE = (24, 30)


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
        self.formalizing = False
        self.samples = {}
        self.groups = {}
        self.sample_groups = {}
        self.row_samples = {}
        self._reference_keys = ()
        self._reference_card_id = None
        self._reference_mode = False
        self._preview_key = None
        self.group_choices = {}
        self._selected_keys = ()
        self._selection_cleared = False
        self.selected = None
        self._results = Queue()
        self._scan_busy = False
        self._history_pending = True
        self._history_scan = False
        self._action_busy = False
        self._images = {}
        self._thumbnail_images = OrderedDict()
        self._bottom_resize_stretch = None
        self._controls_request = None
        self._thumbnail_busy = False
        self._thumbnail_after = None
        self._frame_preview = None
        self._selection_version = 0
        self._revision = 0
        self._closed = False
        self._details_expanded = False
        self._table_height = 220
        self._details_height = 120
        self._normal_details_height = 120
        self._after_ids = set()
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.summary_var = tk.StringVar(value="正在读取卡牌样本……")
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, textvariable=self.summary_var).pack(side="left")
        self.catalog_button = ttk.Button(
            header, text="样本库", bootstyle="link", padding=(6, 0), command=self._toggle_catalog
        )
        self.catalog_button.pack(side="right")
        tools = ttk.Frame(self)
        self.review_tools = tools
        tools.grid(row=1, column=0, sticky="ew", pady=4)
        filters = ttk.Frame(tools)
        filters.pack(fill="x")
        selection_tools = ttk.Frame(tools)
        self.filters_panel = filters
        self.selection_tools = selection_tools
        selection_tools.pack(fill="x", pady=(4, 0))
        self.filter_var = tk.StringVar(value="待确认")
        self.stage_tabs = ttk.Notebook(tools, height=1)
        self.stage_pages = {}
        for label in REVIEW_TABS:
            page = ttk.Frame(self.stage_tabs)
            self.stage_tabs.add(page, text=f"{label}（0）")
            self.stage_pages[label] = page
        self.stage_tabs.pack(fill="x", before=filters, pady=(0, 4))
        self.stage_tabs.bind("<<NotebookTabChanged>>", self._switch_tab)
        self.group_var = tk.StringVar(value="全部分组")
        self.group_combo = ttk.Combobox(
            filters, textvariable=self.group_var, values=["全部分组"], state="readonly", width=16
        )
        self.group_combo.pack(side="left", padx=(8, 0))
        self.group_combo.bind("<<ComboboxSelected>>", lambda _: self._render_list())
        ttk.Button(filters, text="刷新", command=self.refresh).pack(side="left", padx=8)
        ttk.Button(filters, text="打开样本目录", command=self._open_folder).pack(side="left")
        self.expand_details_button = ttk.Button(filters, text="展开详情", command=self._toggle_details)
        self.expand_details_button.pack(side="right")
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
        self.upload_button = ttk.Button(selection_tools, text="上传", command=self._upload, state="disabled")
        self.upload_button.pack(side="left")
        self.clear_button = ttk.Button(
            selection_tools, text="删除样本", bootstyle="danger-outline", command=self._clear_samples, state="disabled"
        )
        self.clear_button.pack(side="right", padx=(24, 0))
        self.body_panes = tk.PanedWindow(
            self,
            orient="vertical",
            sashwidth=6,
            sashpad=0,
            sashrelief="flat",
            sashcursor="sb_v_double_arrow",
            showhandle=False,
            opaqueresize=True,
            borderwidth=0,
        )
        self.body_panes.grid(row=2, column=0, sticky="nsew")
        self.body_panes.bind("<ButtonPress-1>", self._begin_bottom_resize, add="+")
        self.body_panes.bind("<ButtonRelease-1>", self._end_bottom_resize, add="+")
        self.review_panes = tk.PanedWindow(
            self.body_panes,
            orient="vertical",
            sashwidth=8,
            sashpad=0,
            sashrelief="flat",
            showhandle=False,
            opaqueresize=True,
            borderwidth=0,
            background=ttk.Style().colors.border,
        )
        self.bind(
            "<<ThemeChanged>>",
            self._update_review_theme,
            add="+",
        )
        self._update_review_theme()
        self.body_panes.add(self.review_panes, minsize=80, stretch="always")
        table = ttk.Frame(self.review_panes)
        self.sample_table = table
        table.columnconfigure(0, weight=1)
        table.rowconfigure(1, weight=1)
        self.review_panes.add(table, minsize=80, height=220, stretch="always")
        self.selection_var = tk.StringVar(value="已选 0 张；Ctrl / Shift 多选，卡名和备注应用于全部选中样本。")
        selection_label = ttk.Label(table, textvariable=self.selection_var)
        selection_label.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        selection_label.bind("<Configure>", lambda event: selection_label.configure(wraplength=max(1, event.width)))
        self.rows_frame = tk.Frame(table, width=1, height=180, borderwidth=0, highlightthickness=1)
        self.rows_frame.grid(row=1, column=0, columnspan=3, sticky="nsew")
        self.rows_frame.grid_propagate(False)
        self.rows_frame.columnconfigure(2, weight=1)
        self.rows_frame.rowconfigure(0, weight=1)
        self.text_frame = ttk.Frame(self.rows_frame, width=1, height=1)
        self.text_frame.grid(row=0, column=1, sticky="ns")
        self.text_frame.grid_propagate(False)
        self.text_frame.columnconfigure(0, weight=1)
        self.text_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            self.text_frame,
            show="headings",
            height=6,
            selectmode="extended",
            style="CardSamples.Treeview",
        )
        self._configure_columns()
        self.thumbnail_tree = ttk.Treeview(
            self.rows_frame, show="tree headings", height=6, selectmode="extended", style="CardSamples.Treeview"
        )
        self.thumbnail_tree.heading("#0", text="缩略图", anchor="center")
        self.thumbnail_tree.column("#0", stretch=False, anchor="center")
        self.row_scroll = ttk.Scrollbar(self.rows_frame, orient="vertical", command=self._scroll_rows)
        horizontal = ttk.Scrollbar(self.rows_frame, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, columnspan=3, sticky="ew")
        self.tree.configure(xscrollcommand=horizontal.set)
        self.tree.configure(yscrollcommand=self._tree_scrolled)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.thumbnail_tree.grid(row=0, column=0, sticky="ns")
        self.row_scroll.grid(row=0, column=3, rowspan=2, sticky="ns")
        self.rows_frame.bind("<Configure>", self._layout_rows, add="+")
        self._update_review_theme()
        self.description_var = tk.StringVar(value="选择样本可查看完整说明。")
        self.description_label = ttk.Label(table, textvariable=self.description_var, justify="left", wraplength=500)
        self.description_label.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        self.description_label.bind(
            "<Configure>", lambda event: self.description_label.configure(wraplength=max(1, event.width))
        )
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.tree.bind("<Control-a>", self._select_all)
        self.thumbnail_tree.bind("<<TreeviewSelect>>", self._select_thumbnail)
        self.thumbnail_tree.bind("<Control-a>", self._select_all)
        for tree in (self.tree, self.thumbnail_tree):
            tree.bind("<Button-1>", self._fixed_table_pointer)
            tree.bind("<Motion>", self._fixed_table_pointer)
        self.thumbnail_tree.bind("<MouseWheel>", self._thumbnail_wheel)
        self.thumbnail_tree.bind("<Button-4>", lambda _: self._thumbnail_scroll(-1))
        self.thumbnail_tree.bind("<Button-5>", lambda _: self._thumbnail_scroll(1))
        self.thumbnail_tree.bind("<Map>", lambda _: self._queue_thumbnails())
        self.details = ttk.Notebook(self.review_panes)
        self.review_panes.add(self.details, minsize=0, height=60, stretch="never")
        self.reference_panel = ttk.Labelframe(self.details, text="同卡正式参考", padding=4)
        self.reference_panel.columnconfigure(0, weight=1)
        self.reference_panel.rowconfigure(0, weight=1)
        self.reference_tree = ttk.Treeview(
            self.reference_panel,
            columns=("reference", "reason"),
            show="tree headings",
            selectmode="browse",
            height=6,
            style="CardReferences.Treeview",
        )
        self.reference_tree.heading("#0", text="卡牌")
        self.reference_tree.column("#0", width=64, minwidth=64, stretch=False)
        self.reference_tree.heading("reference", text="参考")
        self.reference_tree.column("reference", width=100, minwidth=80, stretch=False)
        self.reference_tree.heading("reason", text="入库说明")
        self.reference_tree.column("reason", width=250)
        reference_scroll = ttk.Scrollbar(self.reference_panel, orient="vertical", command=self.reference_tree.yview)
        reference_scroll.grid(row=0, column=1, sticky="ns")

        def reference_scrolled(first, last):
            reference_scroll.set(first, last)
            self._queue_thumbnails()

        self.reference_tree.configure(yscrollcommand=reference_scrolled)
        self.reference_tree.grid(row=0, column=0, sticky="nsew")
        self.reference_tree.bind("<<TreeviewSelect>>", self._select_reference)
        self.reference_tree.bind("<Map>", lambda _: self._queue_thumbnails())
        self.details.add(self.reference_panel, text="参考列表")
        info = ScrolledFrame(self.details, padding=8, height=220)
        self.info_panel = info
        frame = ttk.Frame(self.details, padding=4)
        self.frame_panel = frame
        self.details.add(info.container, text="手牌与识别信息")
        self.details.add(frame, text="完整战斗图")
        self.details.hide(self.reference_panel)
        self.details.bind("<<NotebookTabChanged>>", self._details_changed)
        self.card_image = ttk.Label(info, text="选择样本查看截图", anchor="center", width=10)
        self.card_image.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 12))
        self.detail_var = tk.StringVar(value="暂无待确认样本时，对战会正常继续。")
        ttk.Label(info, textvariable=self.detail_var, wraplength=320, justify="left").grid(row=0, column=1, sticky="nw")
        self.hand_image = ttk.Label(info, anchor="w")
        self.hand_image.grid(row=1, column=1, columnspan=2, sticky="sw", pady=(8, 0))
        comparison = ttk.Frame(info)
        comparison.grid(row=0, column=2, sticky="n", padx=(12, 0))
        self.reference_image = ttk.Label(comparison, text="正式参考对照", anchor="center", width=18)
        self.reference_image.pack()
        self.reference_var = tk.StringVar()
        ttk.Label(comparison, textvariable=self.reference_var, wraplength=180, justify="left").pack(pady=4)
        info.columnconfigure(1, weight=1)
        self.frame_image = ttk.Label(frame, anchor="center")
        self.frame_image.pack(fill="both", expand=True)
        self.frame_image.bind("<Configure>", lambda _: self._resize_frame())
        self.controls = ScrolledFrame(self.body_panes, padding=0, autohide=True, height=160)
        self.controls.columnconfigure(0, weight=1)
        self.body_panes.add(self.controls.container, minsize=24, height=160, stretch="never")
        edit = ttk.Frame(self.controls)
        self.edit_panel = edit
        edit.grid(row=0, column=0, sticky="ew", pady=2)
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
        notes = ttk.Frame(self.controls)
        self.notes_panel = notes
        notes.grid(row=1, column=0, sticky="ew", pady=2)
        ttk.Label(notes, text="处理备注").pack(side="left")
        self.note_var = tk.StringVar()
        ttk.Entry(notes, textvariable=self.note_var).pack(side="left", fill="x", expand=True, padx=8)
        actions = ttk.Frame(self.controls)
        actions.grid(row=2, column=0, sticky="ew", pady=2)
        self.stage_buttons = {
            "confirm": ttk.Button(actions, text="确认并入库", command=self._save, state="disabled"),
            "ignore": ttk.Button(
                actions, text="忽略", command=lambda: self._change_result("ignored"), state="disabled"
            ),
            "restore": ttk.Button(
                actions, text="恢复待确认", command=lambda: self._change_result("pending"), state="disabled"
            ),
            "rename": ttk.Button(actions, text="更正卡名", command=self._rename_selected, state="disabled"),
            "remove": ttk.Button(actions, text="移出正式参考", command=self._remove_selected, state="disabled"),
        }
        self.stage_help = tk.StringVar()
        ttk.Label(self.controls, textvariable=self.stage_help, wraplength=700).grid(row=3, column=0, sticky="w", pady=2)
        self.message_var = tk.StringVar(value="核对卡名及版本后确认并入库；系统自动去重，存在冲突时保留待确认。")
        message = ttk.Label(self.controls, textvariable=self.message_var, wraplength=640)
        message.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        message.bind("<Configure>", lambda event: message.configure(wraplength=max(200, event.width - 8)))
        self.controls.bind("<Configure>", self._fit_controls, add="+")
        self._later(30, self._fit_controls)
        self._later(100, self._poll)
        self._later(5000, self._auto_refresh)
        self.library_view = CardCatalogView(self, background=self._background, later=self._later, refresh=self.refresh)
        self._later(0, self.refresh)

    def _update_review_theme(self, _event=None):
        style = ttk.Style()
        self.review_panes.configure(background=style.colors.bg)
        self.body_panes.configure(background=style.colors.border)
        for name in ("CardSamples.Treeview", "CardReferences.Treeview"):
            for suffix, fallback in (("", "TkDefaultFont"), (".Heading", "TkHeadingFont")):
                font = Font(root=self, font=style.lookup(f"Treeview{suffix}", "font") or fallback).actual()
                style.configure(
                    f"{name}{suffix}",
                    font=(font["family"], max(8, abs(font["size"]) - 1), font["weight"]),
                )
            style.configure(name, rowheight=REVIEW_THUMBNAIL_SIZE[1] + 4, indent=0)
            style.layout(name, [("Treeview.treearea", {"sticky": "nsew"})])
        if hasattr(self, "rows_frame"):
            heading_font = Font(root=self, font=style.lookup("CardSamples.Treeview.Heading", "font") or "TkHeadingFont")
            thumbnail_width = max(64, heading_font.measure(self.thumbnail_tree.heading("#0", "text")) + 24)
            self.thumbnail_tree.column("#0", width=thumbnail_width, minwidth=thumbnail_width)
            self.rows_frame.configure(
                background=style.colors.inputbg,
                highlightbackground=style.colors.border,
                highlightcolor=style.colors.border,
            )
            self._later(0, self._layout_rows)

    def _layout_rows(self, _event=None):
        text_width = sum(self.tree.column(column, "width") for column in self.tree.cget("columns"))
        side_width = self.thumbnail_tree.winfo_reqwidth() + self.row_scroll.winfo_reqwidth()
        width = max(1, min(text_width, self.rows_frame.winfo_width() - side_width - 2))
        if int(self.text_frame.cget("width")) != width:
            self.text_frame.configure(width=width)

    def _fit_controls(self, _event=None):
        height = self.controls.winfo_reqheight()
        if self._controls_request != height:
            self._controls_request = height
            self.body_panes.paneconfigure(self.controls.container, height=min(height, 140))

    def _begin_bottom_resize(self, event):
        if self.body_panes.identify(event.x, event.y):
            self._bottom_resize_stretch = self.review_panes.panecget(self.details, "stretch")
            details_height = self.details.winfo_height()
            if not self._details_expanded:
                self.review_panes.paneconfigure(
                    self.sample_table, height=self.sample_table.winfo_height(), stretch="never"
                )
            self.review_panes.paneconfigure(self.details, height=details_height, stretch="always")

    def _end_bottom_resize(self, _event):
        if self._bottom_resize_stretch is not None:
            if not self._details_expanded:
                self.review_panes.paneconfigure(
                    self.sample_table, height=self.sample_table.winfo_height(), stretch="always"
                )
            self.review_panes.paneconfigure(
                self.details, height=self.details.winfo_height(), stretch=self._bottom_resize_stretch
            )
            self._bottom_resize_stretch = None

    def _scroll_rows(self, *args):
        self.tree.yview(*args)

    def _tree_scrolled(self, first, last):
        self.row_scroll.set(first, last)
        self.thumbnail_tree.yview_moveto(first)
        self._queue_thumbnails()

    def _thumbnail_scroll(self, steps):
        self.tree.yview_scroll(steps, "units")
        return "break"

    def _thumbnail_wheel(self, event):
        steps = max(1, abs(event.delta) // 120)
        return self._thumbnail_scroll(-steps if event.delta > 0 else steps)

    def _fixed_table_pointer(self, event):
        tree = event.widget
        if tree.identify_region(event.x, event.y) in {"heading", "separator"}:
            tree.configure(cursor="")
            return "break"
        return None

    def _sync_thumbnail_selection(self):
        selection = self.tree.selection()
        if self.thumbnail_tree.selection() != selection:
            self.thumbnail_tree.selection_set(selection)
        focus = self.tree.focus()
        if focus and self.thumbnail_tree.exists(focus):
            self.thumbnail_tree.focus(focus)

    def _select_thumbnail(self, _event=None):
        selection = self.thumbnail_tree.selection()
        if selection == self.tree.selection():
            return
        self.tree.selection_set(selection)
        focus = self.thumbnail_tree.focus()
        if focus and self.tree.exists(focus):
            self.tree.focus(focus)
            self.tree.see(focus)
        self._select()

    def _queue_thumbnails(self):
        if not self._closed and self._thumbnail_after is None:
            self._thumbnail_after = self._later(30, self._load_visible_thumbnails)

    def _load_visible_thumbnails(self):
        self._thumbnail_after = None
        if self._thumbnail_busy or not (self.sample_table.winfo_ismapped() or self.reference_tree.winfo_ismapped()):
            return
        targets = {}
        for tree in (self.tree, self.reference_tree):
            if not tree.winfo_ismapped():
                continue
            keys = tree.get_children()
            position = tree.yview()
            start = int(float(position[0]) * len(keys))
            end = min(len(keys), int(float(position[-1]) * len(keys)) + 2)
            for key in keys[start:end]:
                if key in self._thumbnail_images:
                    self._thumbnail_images.move_to_end(key)
                    self._show_thumbnail(key)
                elif key in self.samples and len(targets) < 32:
                    targets[key] = self.samples[key]
        if targets:
            self._thumbnail_busy = True
            self._background(
                "thumbnails",
                lambda: {key: load_card_thumbnail(sample) for key, sample in targets.items()},
                self._revision,
            )

    def _show_thumbnail(self, key):
        for tree in (self.thumbnail_tree, self.reference_tree):
            if tree.exists(key):
                photo = self._thumbnail_images.get(key)
                tree.item(key, image=photo or "", text="" if photo else "无图")

    def _render_thumbnails(self, keys):
        if self.thumbnail_tree.get_children() != keys:
            self.thumbnail_tree.delete(*self.thumbnail_tree.get_children())
            for key in keys:
                self.thumbnail_tree.insert("", "end", iid=key, text="加载中")
        visible = set(keys) | set(self.reference_tree.get_children())
        for key in list(self._thumbnail_images):
            if key not in visible:
                del self._thumbnail_images[key]
            else:
                self._show_thumbnail(key)
        self.thumbnail_tree.yview_moveto(self.tree.yview()[0])
        self._queue_thumbnails()

    def _toggle_details(self):
        if self._details_expanded:
            self.review_panes.paneconfigure(
                self.details,
                height=self._details_height,
                stretch="always" if self.filter_var.get() == "待上传" else "never",
            )
            self.review_panes.add(
                self.sample_table, before=self.details, minsize=80, height=self._table_height, stretch="always"
            )
        else:
            self._table_height = self.sample_table.winfo_height()
            self._details_height = self.details.winfo_height()
            self.review_panes.forget(self.sample_table)
            self.review_panes.paneconfigure(self.details, stretch="always")
        self._details_expanded = not self._details_expanded
        self._configure_detail_mode()
        self.expand_details_button.configure(text="恢复列表" if self._details_expanded else "展开详情")

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
            history = self._history_pending and not self._running
            self._history_scan = history
            if history:
                self._action_busy = self.formalizing = True
                self._set_action_state()
                if self.clear_busy_callback:
                    self.clear_busy_callback(True)
            root = self.root
            self._background("scan", lambda: self._scan_contents(root, history), self._revision)

    @staticmethod
    def _scan_contents(root, history):
        samples, errors, groups = scan_review(root, refresh_library=True, reassess_history=history)
        return samples, errors, groups, build_catalog(samples)

    def _open_folder(self):
        root = str(self.root)
        self._background("folder", lambda: open_folder(root))

    def _switch_tab(self, _event=None):
        index = self.stage_tabs.index("current")
        self.filter_var.set(tuple(REVIEW_TABS)[index])
        self.group_var.set("全部分组")
        self._selection_cleared = False
        self._render_list()

    def _toggle_catalog(self):
        if self.filter_var.get() == "样本库":
            self._switch_tab()
        else:
            self.filter_var.set("样本库")
            self._render_list()

    def set_running(self, running):
        self._running = running
        self._set_action_state()

    def _selected_clear_keys(self):
        visible = set(self.tree.get_children())
        return {key for key in self.tree.selection() if key in visible and key in self.samples}

    def _clear_samples(self):
        if self._running or self._action_busy:
            return
        samples = self._action_samples()
        if not samples:
            self.message_var.set("请先选择要删除的样本。")
            return
        if (
            not messagebox.askyesno(
                "删除选中样本",
                f"删除选中的 {len(samples)} 张样本及其截图、标注和本地运行记录？\n"
                "已加入的正式参考保留。删除样本无法撤销；移出正式参考请使用独立按钮。",
                parent=self,
                default=messagebox.NO,
            )
            or self._running
            or self._action_busy
        ):
            return
        self._start_action(lambda: f"已删除 {delete_samples(self.root, samples)} 张样本，正式参考保留。", clearing=True)

    def _select_all(self, _event=None):
        if not self._action_busy:
            rows = self.tree.get_children()
            self.tree.selection_set(rows)
            if rows and self.tree.focus() not in rows:
                self.tree.focus(rows[0])
            self._select()
        return "break"

    def _upload_samples(self):
        if self.filter_var.get() != "待上传":
            return []
        return [sample for key in self.tree.selection() for sample in self.row_samples.get(key, [])]

    def _upload(self):
        if self._running or self._action_busy:
            return
        samples = self._upload_samples()
        if not samples:
            self.message_var.set("请在待上传列表选择要上传的卡牌。")
            return
        results, root = self._results, self.root

        def progress(message):
            results.put(("progress", None, message, None))

        self._start_action(lambda: upload_references(root, samples, progress=progress))

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
                if self._history_scan:
                    self._history_scan = False
                    self._history_pending = error is not None or bool(result[1])
                    self._action_busy = self.formalizing = False
                    if self.clear_busy_callback:
                        self.clear_busy_callback(False)
                    self._set_action_state()
                if token != self._revision:
                    self.refresh()
                    continue
                if error is None:
                    samples, errors, groups, catalog = result
                    self.samples = {sample.key: sample for sample in samples}
                    self.library_view.set_data(catalog)
                    self._update_groups(groups)
                    counts = Counter(sample.review.get("work_result", "pending") for sample in samples)
                    included_cards = len(
                        {sample.card_id for sample in samples if sample.status == "included" and not sample.uploaded}
                    )
                    self.summary_var.set(
                        "  |  ".join(
                            f"{label} {included_cards} 种卡牌"
                            if result == "included"
                            else f"{label} {counts[result]} 张"
                            for label, result in REVIEW_TABS.items()
                        )
                    )
                    for label, page in self.stage_pages.items():
                        count = included_cards if label == "待上传" else counts[REVIEW_TABS[label]]
                        self.stage_tabs.tab(page, text=f"{label}（{count}）")
                    if self.count_callback:
                        self.count_callback(counts["pending"])
                    self._render_list()
                    if errors:
                        self.message_var.set(f"有 {errors} 条不完整或无效记录已跳过，可在写入完成后刷新。")
            elif kind.startswith("catalog_"):
                self.library_view.deliver(kind, token, result, error)
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
                    comparison = result.get("comparison", {})
                    preview = comparison.get("image")
                    photo = ImageTk.PhotoImage(preview, master=self) if preview is not None else None
                    self._images["reference"] = photo
                    self.reference_image.configure(image=photo or "", text="参考原图不可用" if photo is None else "")
                    self.reference_var.set(comparison.get("text", ""))
            elif kind == "thumbnails":
                self._thumbnail_busy = False
                if error is None and token == self._revision:
                    for key, preview in result.items():
                        if not self.thumbnail_tree.exists(key) and not self.reference_tree.exists(key):
                            continue
                        compact = preview.copy() if preview is not None else None
                        if compact is not None:
                            compact.thumbnail(REVIEW_THUMBNAIL_SIZE)
                        self._thumbnail_images[key] = (
                            ImageTk.PhotoImage(compact, master=self) if compact is not None else None
                        )
                        self._thumbnail_images.move_to_end(key)
                        self._show_thumbnail(key)
                    while len(self._thumbnail_images) > THUMBNAIL_CACHE_SIZE:
                        self._thumbnail_images.popitem(last=False)
                self._queue_thumbnails()
            elif kind == "progress":
                self.message_var.set(result)
            elif kind == "save":
                self._action_busy = self.formalizing = self.clearing = False
                if self.clear_busy_callback:
                    self.clear_busy_callback(False)
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

    def _configure_columns(self):
        columns = FILTER_COLUMNS[self.filter_var.get()]
        if tuple(self.tree.cget("columns")) != columns:
            self.tree.configure(columns=columns, displaycolumns="#all")
            heading_font = Font(
                root=self, font=ttk.Style().lookup("CardSamples.Treeview.Heading", "font") or "TkHeadingFont"
            )
            for column in columns:
                title, width = COLUMN_SPECS[column]
                width = max(width, heading_font.measure(title) + 20)
                self.tree.heading(column, text=title, anchor="w")
                self.tree.column(column, width=width, minwidth=width, stretch=False, anchor="w")
        return columns

    def _render_list(self):
        if self.filter_var.get() == "样本库":
            self.body_panes.grid_remove()
            self.review_tools.grid_remove()
            self.catalog_button.configure(text="返回样本管理")
            self.filters_panel.pack_forget()
            self.selection_tools.pack_forget()
            self.library_view.grid(row=2, column=0, sticky="nsew")
            self.library_view._queue_thumbnails()
            self._set_action_state()
            return
        self.library_view.grid_remove()
        self.review_tools.grid()
        self.catalog_button.configure(text="样本库")
        self.filters_panel.pack(fill="x")
        self.selection_tools.pack(fill="x", pady=(4, 0))
        self.body_panes.grid()
        self._configure_detail_mode()
        selected = self.selected.key if self.selected else None
        selection = self.tree.selection()
        pending = self.filter_var.get() == "待确认"
        if not pending:
            self.group_var.set("全部分组")
        self.group_combo.configure(state="readonly" if pending else "disabled")
        group_filter = self.group_choices.get(self.group_var.get())
        columns = self._configure_columns()
        result = REVIEW_TABS[self.filter_var.get()]
        visible = [
            self.samples[key]
            for group, keys in self.groups.items()
            for key in keys
            if (group_filter is None or group == group_filter)
            and self.samples[key].review.get("work_result", "pending") == result
            and (result != "included" or not self.samples[key].uploaded)
        ]
        # Keep groups in collection order; bring explicit conflicts to the front.
        visible.sort(key=lambda sample: "复核" not in validation_reason(sample))
        self.row_samples = {}
        if result == "included":
            by_card = {}
            for sample in visible:
                by_card.setdefault(sample.card_id, []).append(sample)
            for sources in by_card.values():
                self.row_samples[sources[0].key] = sources
            visible = [sources[0] for sources in by_card.values()]
            selected = next(
                (key for key, sources in self.row_samples.items() if any(item.key == selected for item in sources)),
                None,
            )
        else:
            self.row_samples = {sample.key: [sample] for sample in visible}
        desired = tuple(sample.key for sample in visible)
        if tuple(self.tree.get_children()) != desired:
            self.tree.delete(*self.tree.get_children())
        labels = {group: label.removeprefix("相似组 ") for label, group in self.group_choices.items()}
        for sample in visible:
            candidates = sample.record.get("candidates", [])
            suggestion = CARD_NAMES_ZH.get(candidates[0].get("card_id"), "") if candidates else ""
            card = CARD_NAMES_ZH.get(sample.card_id, "") or (f"候选：{suggestion}" if suggestion else "待选择")
            row = {
                "group": labels[self.sample_groups[sample.key]],
                "card": card,
                "reason": REASON_LABELS.get(sample.record.get("reason"), "识别异常"),
                "status": sample_progress(sample)[0],
                "validation_reason": validation_reason(sample),
                "references": "",
            }
            if result == "included":
                sources = self.row_samples[sample.key]
                count = len({source.review.get("formal_id") or source.key for source in sources})
                row["references"] = f"{count} 份"
            values = tuple(row[column] for column in columns)
            if self.tree.exists(sample.key):
                self.tree.item(sample.key, values=values)
            else:
                self.tree.insert("", "end", iid=sample.key, values=values)
        self._layout_rows()
        self._render_thumbnails(desired)
        remaining = tuple(key for key in selection if key in desired)
        if remaining:
            self.tree.selection_set(remaining)
        if self._selection_cleared and not selection:
            self._set_action_state()
            return
        if selected in desired:
            if result == "included":
                if not self.tree.selection():
                    self.tree.selection_set(selected)
                self.tree.focus(selected)
                self._select()
                return
            if self.selected is not None and self.selected.review.get("reviewed_at") != self.samples[
                selected
            ].review.get("reviewed_at"):
                self._selected_keys = ()
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
            if result == "included":
                self._render_references(())
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
            self.reference_image.configure(image="", text="暂无参考对照")
            self.reference_var.set("")
            self._configure_detail_mode()
            self._set_action_state()

    def _select(self, _event=None):
        self._sync_thumbnail_selection()
        selection = self.tree.selection()
        if self.filter_var.get() == "待上传":
            self._render_references(selection)
            return
        self._show_samples(selection)

    def _render_references(self, selection):
        sources = self.row_samples.get(selection[0], []) if len(selection) == 1 else []
        card_id = sources[0].card_id if sources else None
        if card_id != self._reference_card_id:
            self._reference_card_id = card_id
            self.details.select(self.reference_panel)
        unique: dict[str, ReviewSample] = {}
        for sample in sources:
            unique.setdefault(sample.review.get("formal_id") or sample.key, sample)
        keys = tuple(sample.key for sample in unique.values())
        previous = self.selected.key if self.selected else None
        if self._reference_keys != keys:
            self.reference_tree.delete(*self.reference_tree.get_children())
            self._reference_keys = keys
            for index, sample in enumerate(unique.values(), 1):
                self.reference_tree.insert(
                    "", "end", iid=sample.key, text="加载中", values=(f"参考 {index}", validation_reason(sample))
                )
        else:
            for index, sample in enumerate(unique.values(), 1):
                self.reference_tree.item(sample.key, values=(f"参考 {index}", validation_reason(sample)))
        card = CARD_NAMES_ZH.get(sources[0].card_id, sources[0].card_id) if sources else "请选择一张卡牌"
        self.reference_panel.configure(text=f"{card} · 正式参考 {len(keys)} 份")
        if keys:
            key = previous if previous in keys else keys[0]
            if self.reference_tree.selection() != (key,):
                self.reference_tree.selection_set(key)
                self.reference_tree.focus(key)
                self.reference_tree.see(key)
            self._show_samples((key,))
            for key in keys:
                if key in self._thumbnail_images:
                    self._show_thumbnail(key)
            self._queue_thumbnails()
        else:
            self._show_samples(())

    def _select_reference(self, _event=None):
        if self.filter_var.get() == "待上传" and len(self.tree.selection()) == 1:
            self._show_samples(self.reference_tree.selection())

    def _show_samples(self, selection):
        if not selection:
            self._selected_keys = ()
            self.selected = None
            self._selection_version += 1
            self.description_var.set("选择样本可查看完整说明。")
            if self.filter_var.get() == "待上传":
                self._images.clear()
                self._frame_preview = None
                for widget in (self.card_image, self.hand_image, self.frame_image, self.reference_image):
                    widget.configure(image="", text="请选择具体参考")
                self.reference_var.set("")
                self.detail_var.set("在上方选择一个卡名，再在参考展示框中选择具体参考。")
            self._set_action_state()
            return
        self._selection_cleared = False
        focus = self.reference_tree.focus() if self.filter_var.get() == "待上传" else self.tree.focus()
        key = focus if focus in selection else selection[0]
        if key not in self.samples:
            return
        sample = self.samples[key]
        if (
            self.selected
            and self.selected.key == sample.key
            and self.selected.card_id == sample.card_id
            and self.selected.review.get("reviewed_at") == sample.review.get("reviewed_at")
            and set(selection) == set(self._selected_keys)
        ):
            self.selected = sample
            self._selected_keys = selection
            self._describe_sample(sample)
            self._request_preview()
            self._set_action_state()
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
        for widget in (self.card_image, self.hand_image, self.frame_image, self.reference_image):
            widget.configure(image="", text="正在读取图片……")
        self._request_preview()
        self._set_action_state()

    def _details_changed(self, _event=None):
        if self._closed:
            return
        self._queue_thumbnails()
        self._request_preview()

    def _request_preview(self):
        if self.selected is None:
            return
        if self.filter_var.get() == "待上传" and self.details.select() == str(self.reference_panel):
            return
        key = (self.selected.key, self._selection_version)
        if key != self._preview_key:
            self._preview_key = key
            sample = self.selected
            self._background("preview", lambda: self._load_details(sample), self._selection_version)

    def _load_details(self, sample):
        result = load_previews(sample)
        try:
            result["comparison"] = reference_details(self.root, sample)
        except (OSError, ValueError) as error:
            result["comparison"] = {"image": None, "text": f"参考对照不可用：{error}"}
        return result

    def _configure_detail_mode(self):
        show_references = self.filter_var.get() == "待上传"
        if show_references:
            self.description_label.grid_remove()
        else:
            self.description_label.grid()
        if show_references != self._reference_mode:
            self._reference_mode = show_references
            if show_references:
                self._normal_details_height = self.details.winfo_height()
                self.details.tab(self.reference_panel, state="normal")
                self.details.select(self.reference_panel)
                if not self._details_expanded:
                    self.review_panes.paneconfigure(self.details, height=260, stretch="always")
            else:
                self.details.hide(self.reference_panel)
                if not self._details_expanded:
                    self.review_panes.paneconfigure(self.details, height=self._normal_details_height, stretch="never")
        editable = self.filter_var.get() in ("待确认", "待上传")
        if editable:
            self.edit_panel.grid()
        else:
            self.edit_panel.grid_remove()
            self._hide_card_suggestions()
        if self.filter_var.get() == "待确认":
            self.notes_panel.grid()
        else:
            self.notes_panel.grid_remove()
        self._later(30, self._fit_controls)

    def _select_group(self):
        if self.selected is None or self._action_busy:
            return
        group = self.sample_groups.get(self.selected.key)
        visible = set(self.tree.get_children())
        keys = [key for key in self.groups.get(group, []) if key in visible]
        self.tree.selection_set(keys)
        self._select()

    def _describe_sample(self, sample):
        self.description_var.set(f"说明：{validation_reason(sample)}")
        verification, _, _ = sample_progress(sample)
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
        if record.get("builtin_result"):
            builtin = record["builtin_result"]
            card = builtin.get("card_id", "UNKNOWN")
            self.detail_var.set(
                self.detail_var.get()
                + f"\n仅正式库：{CARD_NAMES_ZH.get(card, '未知卡牌')}，距离 {builtin.get('distance', '?')}"
                + f"；加入本地样本：{CARD_NAMES_ZH.get(record.get('card_id'), '未知卡牌')}"
            )
        self.detail_var.set(
            self.detail_var.get()
            + f"\n样本：{sample.key}\n处理说明：{validation_reason(sample)}"
            + f"\n备注：{sample.review.get('note') or '无'}"
            + (f"\n正式参考：{sample.review['formal_id']}" if sample.review.get("formal_id") else "")
        )
        if stats := sample.review.get("library_runtime"):
            self.detail_var.set(
                self.detail_var.get()
                + f"\n历史观察：匹配 {stats.get('matches', 0)} 次，补足 {stats.get('assists', 0)} 次"
                + f"，来自 {stats.get('assist_battles', 0)} 个补足对局；不作为入库条件。"
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
        self._sync_thumbnail_selection()
        stage = self.filter_var.get()
        selected = self._action_samples()
        count = len(selected)
        enabled = bool(count) and not self._running and not self._action_busy
        self.selection_var.set(
            f"已选 {len(self.tree.selection())} 种卡牌；请在下方选择具体参考进行操作。"
            if stage == "待上传"
            else f"已选 {count} 张；卡名、版本及备注应用于全部选中样本。Ctrl / Shift 多选。"
        )
        visible = STAGE_ACTIONS[stage]
        owned = bool(selected) and all(sample.review.get("owns_reference") for sample in selected)
        same_reference = len({sample.review.get("formal_id") for sample in selected}) == 1
        for key, button in self.stage_buttons.items():
            button.pack_forget()
            if key in visible:
                button.pack(side="left", padx=(0, 8))
            allowed = enabled and (key not in ("rename", "remove") or owned and same_reference)
            button.configure(state="normal" if allowed else "disabled")
        self.clear_button.configure(state="normal" if enabled else "disabled")
        self.upload_button.configure(
            state="normal" if self._upload_samples() and not self._running and not self._action_busy else "disabled"
        )
        self.select_all_button.configure(
            state="normal" if self.tree.get_children() and not self._action_busy else "disabled"
        )
        self.clear_selection_button.configure(
            state="normal" if self.tree.selection() and not self._action_busy else "disabled"
        )
        self.select_group_button.configure(
            state="normal" if count and not self._action_busy and stage == "待确认" else "disabled"
        )
        self.stage_help.set(
            {
                "待确认": "核对卡名与版本后确认并入库；正式库已覆盖时无需新增，冲突保留待确认。任务停止后可操作。",
                "待上传": "在下方选择具体参考更正或移出；上传共享上方选中卡牌的全部正式参考。截图和备注留在本机。",
                "样本库": "查询全部卡牌的正式参考；选择两份参考可并排对比。",
                "已忽略": "人工忽略的样本；可恢复待确认。",
            }[stage]
        )

    def _action_samples(self):
        if self.filter_var.get() == "样本库":
            return []
        if self.filter_var.get() == "待上传":
            if len(self.tree.selection()) != 1:
                return []
            visible = {sample.key for sample in self.row_samples.get(self.tree.selection()[0], [])}
            return [
                self.samples[key] for key in self.reference_tree.selection() if key in visible and key in self.samples
            ]
        visible = set(self.tree.get_children())
        return [self.samples[key] for key in self.tree.selection() if key in visible and key in self.samples]

    def _start_action(self, operation, *, clearing=False):
        if self._running or self._action_busy:
            return
        self._revision += 1
        self._selection_version += 1
        self._action_busy = self.formalizing = True
        self.clearing = clearing
        self._set_action_state()
        if self.clear_busy_callback:
            self.clear_busy_callback(True)
        self.message_var.set("正在处理选中样本……")
        self._background("save", operation)

    def _chosen_card(self):
        entered = self.card_var.get().strip()
        card_id = self.card_choices.get(entered, entered)
        return next((key for key, name in CARD_NAMES_ZH.items() if name == entered), card_id)

    def _save(self):
        if self._running or self._action_busy or self.filter_var.get() != "待确认":
            return
        samples, card_id, note = self._action_samples(), self._chosen_card(), self.note_var.get()
        if not samples:
            return
        if card_id not in CARD_NAMES_ZH:
            self.message_var.set("请选择名录中的准确卡名，并区分普通／觉醒／精英版本。名录缺失时请保留待确认。")
            return
        self._start_action(lambda: confirm_samples(self.root, samples, card_id, note))

    def _change_result(self, result):
        if self._running or self._action_busy:
            return
        samples, note = self._action_samples(), self.note_var.get()
        if samples:
            self._start_action(lambda: change_result(self.root, samples, result, note))

    def _rename_selected(self):
        samples, card_id = self._action_samples(), self._chosen_card()
        if self._running or self._action_busy or not samples:
            return
        if card_id not in CARD_NAMES_ZH:
            self.message_var.set("请选择名录中的准确卡名，并区分普通／觉醒／精英版本。")
            return
        self._start_action(lambda: edit_formal_reference(self.root, samples, new_card_id=card_id))

    def _remove_selected(self):
        samples = self._action_samples()
        if self._running or self._action_busy or not samples:
            return
        identifier = samples[0].review.get("formal_id")
        linked = sum(sample.review.get("formal_id") == identifier for sample in self.samples.values())
        if (
            not messagebox.askyesno(
                "移出正式参考",
                f"移出所选的 1 份正式参考，并将 {linked} 张关联样本恢复待确认？\n原始截图和备注保留。",
                parent=self,
                default=messagebox.NO,
            )
            or self._running
            or self._action_busy
        ):
            return
        self._start_action(lambda: edit_formal_reference(self.root, samples))
