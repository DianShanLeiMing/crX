"""Read-only card catalog with lazy thumbnails and a single-reference preview."""

from __future__ import annotations

import tkinter as tk
from collections import OrderedDict

import ttkbootstrap as ttk
from PIL import ImageTk

from crX.bot.card_data import CARD_NAMES_ZH
from crX.utils.card_catalog import preview_crop, reference_crop


class CardCatalogView(ttk.Frame):
    def __init__(self, parent, *, background, later, refresh):
        super().__init__(parent)
        self._background = background
        self._later = later
        self.data = {}
        self._signature = None
        self._generation = 0
        self._selection = 0
        self._thumb_busy = False
        self._thumb_queued = False
        self._photos = OrderedDict()
        self._preview_photo = None
        self._references = {}
        self._sort_count = None
        self._card = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        search = ttk.Frame(self)
        search.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(search, text="搜索卡名 / 英文 ID").pack(side="left")
        self.query = tk.StringVar()
        ttk.Entry(search, textvariable=self.query, width=24).pack(side="left", padx=6)
        self.only_samples = tk.BooleanVar(value=False)
        ttk.Checkbutton(search, text="只看有样本", variable=self.only_samples, command=self.render_cards).pack(
            side="left"
        )
        ttk.Button(search, text="刷新", command=refresh).pack(side="right")
        self.query.trace_add("write", lambda *_: self.render_cards())
        self.panes = tk.PanedWindow(self, orient="horizontal", sashwidth=6, opaqueresize=True, borderwidth=0)
        self.panes.grid(row=1, column=0, sticky="nsew")
        left = ttk.Frame(self.panes)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.panes.add(left, minsize=180, width=270, stretch="never")
        self.cards = ttk.Treeview(
            left, columns=("card", "count"), show="tree headings", selectmode="browse", style="CardSamples.Treeview"
        )
        self.cards.heading("#0", text="缩略图")
        self.cards.column("#0", width=54, minwidth=42, stretch=False)
        self.cards.heading("card", text="卡名", command=self._sort_names)
        self.cards.column("card", width=150, minwidth=80)
        self.cards.heading("count", text="样本数", command=self._sort_counts)
        self.cards.column("count", width=60, minwidth=54, stretch=False)
        self.cards.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.cards.yview)
        scroll.grid(row=0, column=1, sticky="ns")

        def scrolled(first, last):
            scroll.set(first, last)
            self._queue_thumbnails()

        self.cards.configure(yscrollcommand=scrolled)
        self.cards.bind("<<TreeviewSelect>>", self._select_card)
        self.cards.bind("<Map>", lambda _: self._queue_thumbnails())
        right = ttk.Frame(self.panes)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        self.panes.add(right, minsize=220, stretch="always")
        self.title = tk.StringVar(value="选择卡牌查看正式参考")
        ttk.Label(right, textvariable=self.title).grid(row=0, column=0, sticky="ew", padx=6, pady=4)
        gallery = ttk.Frame(right)
        gallery.columnconfigure(0, weight=1)
        gallery.rowconfigure(0, weight=1)
        self.references = ttk.Treeview(
            gallery,
            columns=("name",),
            show="tree headings",
            height=4,
            selectmode="browse",
            style="CardReferences.Treeview",
        )
        self.references.heading("#0", text="缩略图")
        self.references.column("#0", width=54, minwidth=42, stretch=False)
        self.references.heading("name", text="参考")
        self.references.column("name", width=120, minwidth=60)
        self.references.grid(row=0, column=0, sticky="nsew")
        reference_scroll = ttk.Scrollbar(gallery, orient="vertical", command=self.references.yview)
        reference_scroll.grid(row=0, column=1, sticky="ns")

        def references_scrolled(first, last):
            reference_scroll.set(first, last)
            self._queue_thumbnails()

        self.references.configure(yscrollcommand=references_scrolled)
        self.references.bind("<<TreeviewSelect>>", self._select_reference)
        gallery.grid(row=1, column=0, sticky="nsew", padx=(6, 0))
        preview = ttk.Labelframe(right, text="参考原图", padding=4)
        preview.grid(row=2, column=0, sticky="nsew", padx=6, pady=(6, 0))
        self.preview_image = ttk.Label(preview, text="选择参考", anchor="center", width=1)
        self.preview_image.pack(fill="both", expand=True)
        self.caption_var = tk.StringVar()
        caption = ttk.Label(preview, textvariable=self.caption_var, wraplength=300, justify="left")
        caption.pack(fill="x", pady=4)
        caption.bind("<Configure>", lambda event: event.widget.configure(wraplength=max(1, event.width)))
        self.message = tk.StringVar(value="样本数按正式参考去重统计；无原图的参考仍可正常参与识别。")
        ttk.Label(self, textvariable=self.message, wraplength=680).grid(row=2, column=0, sticky="ew", pady=(6, 0))

    def set_data(self, data):
        signature = tuple(
            (card, tuple((ref.identifier, ref.uploaded, tuple(sample.key for sample in ref.sources)) for ref in refs))
            for card, refs in data.items()
        )
        self.data = data
        if signature == self._signature:
            return
        self._signature = signature
        self._generation += 1
        self._photos.clear()
        self.render_cards()

    def _sort_counts(self):
        self._sort_count = not self._sort_count if self._sort_count is not None else True
        self.render_cards()

    def _sort_names(self):
        self._sort_count = None
        self.render_cards()

    def render_cards(self):
        query = "".join(self.query.get().casefold().split())
        cards = [
            card
            for card, refs in self.data.items()
            if (not self.only_samples.get() or refs) and query in f"{CARD_NAMES_ZH.get(card, card)}{card}".casefold()
        ]
        cards.sort(
            key=lambda card: (
                -len(self.data[card]) if self._sort_count else len(self.data[card]),
                CARD_NAMES_ZH.get(card, card),
            )
            if self._sort_count is not None
            else CARD_NAMES_ZH.get(card, card)
        )
        self.cards.delete(*self.cards.get_children())
        for card in cards:
            self.cards.insert(
                "", "end", iid=card, text="", values=(CARD_NAMES_ZH.get(card, card), len(self.data[card]))
            )
            self._show_photo(f"card:{card}", self.cards, card)
        if cards:
            selected = self._card if self._card in cards else cards[0]
            self.cards.selection_set(selected)
            self.cards.focus(selected)
            self._select_card()
        else:
            self._card = None
            self._clear_references("没有匹配的卡牌")
        self._queue_thumbnails()

    def _clear_references(self, text):
        self._selection += 1
        self._references = {}
        self.references.delete(*self.references.get_children())
        self.title.set(text)
        self._preview_photo = None
        self.preview_image.configure(image="", text=text)
        self.caption_var.set("")

    def _select_card(self, _event=None):
        selection = self.cards.selection()
        if not selection:
            return
        card = selection[0]
        previous = self.references.selection() if card == self._card else ()
        self._card = card
        refs = self.data.get(card, [])
        self._clear_references("暂无正式参考" if not refs else "请选择参考")
        self.title.set(f"{CARD_NAMES_ZH.get(card, card)} · {len(refs)} 份正式参考")
        number = 0
        for ref in refs:
            if ref.builtin:
                name = ref.status
            else:
                number += 1
                name = f"参考 {number}"
            self._references[ref.identifier] = ref
            self.references.insert("", "end", iid=ref.identifier, values=(name,))
            self._show_photo(f"reference:{ref.identifier}", self.references, ref.identifier)
        if refs:
            selected = tuple(key for key in previous if key in self._references) or (refs[0].identifier,)
            self.references.selection_set(selected)
        self._queue_thumbnails()

    def _select_reference(self, _event=None):
        keys = self.references.selection()
        if not keys:
            self._selection += 1
            self._preview_photo = None
            self.preview_image.configure(image="", text="请选择参考" if self._references else "暂无正式参考")
            self.caption_var.set("")
        elif keys[0] in self._references:
            self._load_preview(keys[0])

    def _load_preview(self, key):
        ref, name = self._references[key], self.references.set(key, "name")
        self._selection += 1
        token = (self._generation, self._selection)
        self._preview_photo = None
        self.preview_image.configure(image="", text="加载中……")
        self.caption_var.set("")
        self._background("catalog_preview", lambda: (ref.identifier, name, preview_crop(ref)), token)

    def _queue_thumbnails(self):
        if not self._thumb_queued:
            self._thumb_queued = True
            self._later(30, self._load_thumbnails)

    def _load_thumbnails(self):
        self._thumb_queued = False
        if self._thumb_busy or not self.winfo_ismapped():
            return
        targets = {}
        for tree, prefix in ((self.cards, "card"), (self.references, "reference")):
            keys = tree.get_children()
            first, last = tree.yview()
            for key in keys[int(first * len(keys)) : min(len(keys), int(last * len(keys)) + 2)]:
                cache_key = f"{prefix}:{key}"
                if cache_key in self._photos:
                    self._show_photo(cache_key, tree, key)
                elif len(targets) < 32:
                    targets[cache_key] = self.data.get(key, []) if prefix == "card" else [self._references[key]]
        if targets:
            self._thumb_busy = True

            def load():
                return {
                    key: next((image for ref in refs if (image := reference_crop(ref)) is not None), None)
                    for key, refs in targets.items()
                }

            self._background("catalog_thumbnails", load, self._generation)

    def _show_photo(self, cache_key, tree, key):
        if cache_key in self._photos and tree.exists(key):
            self._photos.move_to_end(cache_key)
            photo = self._photos[cache_key]
            tree.item(key, image=photo or "", text="" if photo else "无图")

    def deliver(self, kind, token, result, error):
        if kind == "catalog_thumbnails":
            self._thumb_busy = False
            if error is None and token == self._generation:
                for key, preview in result.items():
                    self._photos[key] = ImageTk.PhotoImage(preview, master=self) if preview is not None else None
                    prefix, identifier = key.split(":", 1)
                    self._show_photo(key, self.cards if prefix == "card" else self.references, identifier)
                while len(self._photos) > 128:
                    self._photos.popitem(last=False)
            self._queue_thumbnails()
        elif kind == "catalog_preview" and token == (self._generation, self._selection):
            if error:
                self.message.set("样本原图暂不可用，请刷新后重试。")
                return
            identifier, name, preview = result
            photo = ImageTk.PhotoImage(preview, master=self) if preview is not None else None
            self._preview_photo = photo
            self.preview_image.configure(image=photo or "", text="暂无原图" if photo is None else "")
            self.caption_var.set(f"{name}\n参考编号：{identifier}")
            self.message.set("样本数按正式参考去重统计；无原图的参考仍可正常参与识别。")
