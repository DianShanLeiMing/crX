"""Text-only reference catalog with explicit first-choice and statistics controls."""

from __future__ import annotations

import tkinter as tk

import ttkbootstrap as ttk

from crX.bot.card_data import CARD_NAMES_ZH


class CardCatalogView(ttk.Frame):
    def __init__(self, parent, *, refresh, action=lambda _operation: None, status=None):
        super().__init__(parent)
        self.data = {}
        self._action = action
        self._disabled = False
        self._allow_reorder = True
        self._refresh = refresh
        self._signature = None
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
        ttk.Button(search, text="刷新", command=self.refresh_order).pack(side="right")
        self.query.trace_add("write", lambda *_: self.render_cards())
        self.panes = tk.PanedWindow(self, orient="horizontal", sashwidth=6, opaqueresize=True, borderwidth=0)
        self.panes.grid(row=1, column=0, sticky="nsew")
        left = ttk.Frame(self.panes)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.panes.add(left, minsize=180, width=270, stretch="never")
        self.cards = ttk.Treeview(
            left, columns=("card", "count"), show="headings", selectmode="browse", style="CardSamples.Treeview"
        )
        self.cards.heading("card", text="卡名", command=self._sort_names)
        self.cards.column("card", width=150, minwidth=80)
        self.cards.heading("count", text="样本数", command=self._sort_counts)
        self.cards.column("count", width=60, minwidth=54, stretch=False)
        self.cards.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.cards.yview)
        scroll.grid(row=0, column=1, sticky="ns")

        self.cards.configure(yscrollcommand=scroll.set)
        self.cards.bind("<<TreeviewSelect>>", self._select_card)
        right = ttk.Frame(self.panes)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        self.panes.add(right, minsize=220, stretch="always")
        self.title = tk.StringVar(value="选择卡牌查看正式参考")
        ttk.Label(right, textvariable=self.title).grid(row=0, column=0, sticky="ew", padx=6, pady=4)
        tools = ttk.Frame(right)
        tools.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 4))
        self.preferred_button = ttk.Button(tools, text="设为首选 / 交换", command=lambda: action("preferred"))
        self.preferred_button.grid(row=0, column=0, sticky="w")
        self.upload_button = ttk.Button(tools, text="上传该卡牌", command=lambda: action("upload"))
        self.upload_button.grid(row=0, column=1, sticky="w", padx=6)
        self.update_hits = tk.BooleanVar(value=False)
        self.hits_check = ttk.Checkbutton(
            tools, text="更新命中数", variable=self.update_hits, command=self._update_message
        )
        self.hits_check.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.clear_button = ttk.Button(
            tools, text="清空全部本地命中数", bootstyle="secondary-outline", command=lambda: action("clear")
        )
        self.clear_button.grid(row=1, column=1, sticky="w", padx=6, pady=(4, 0))
        gallery = ttk.Frame(right)
        gallery.columnconfigure(0, weight=1)
        gallery.rowconfigure(0, weight=1)
        self.references = ttk.Treeview(
            gallery,
            columns=("name", "identifier", "hits"),
            show="headings",
            height=4,
            selectmode="browse",
            style="CardReferences.Treeview",
        )
        self.references.heading("name", text="参考")
        self.references.column("name", width=120, minwidth=60)
        self.references.heading("identifier", text="参考编号")
        self.references.column("identifier", width=160, minwidth=100)
        self.references.heading("hits", text="命中数")
        self.references.column("hits", width=80, minwidth=60, stretch=False, anchor="e")
        self.references.grid(row=0, column=0, sticky="nsew")
        reference_scroll = ttk.Scrollbar(gallery, orient="vertical", command=self.references.yview)
        reference_scroll.grid(row=0, column=1, sticky="ns")

        self.references.configure(yscrollcommand=reference_scroll.set)
        reference_horizontal = ttk.Scrollbar(gallery, orient="horizontal", command=self.references.xview)
        reference_horizontal.grid(row=1, column=0, sticky="ew")
        self.references.configure(xscrollcommand=reference_horizontal.set)
        gallery.grid(row=2, column=0, sticky="nsew", padx=(6, 0))
        self.message = tk.StringVar(value="样本数按颜色指纹去重统计；上传成功后自动清理来源图片。")
        ttk.Label(self, textvariable=self.message, wraplength=680).grid(row=2, column=0, sticky="ew", pady=(6, 0))
        if status is not None:
            ttk.Label(self, textvariable=status, wraplength=680).grid(row=3, column=0, sticky="ew", pady=(4, 0))

    def set_data(self, data):
        if not self._allow_reorder:
            for card, refs in data.items():
                old_order = {ref.identifier: index for index, ref in enumerate(self.data.get(card, []))}
                refs.sort(key=lambda ref: (not ref.preferred, old_order.get(ref.identifier, len(old_order))))
        self._allow_reorder = False
        same_order = tuple((card, tuple(ref.identifier for ref in refs)) for card, refs in data.items()) == tuple(
            (card, tuple(ref.identifier for ref in refs)) for card, refs in self.data.items()
        )
        signature = tuple(
            (
                card,
                tuple(
                    (
                        ref.identifier,
                        ref.uploaded,
                        ref.name,
                        ref.hits,
                        ref.cleared,
                        tuple(sample.key for sample in ref.sources),
                    )
                    for ref in refs
                ),
            )
            for card, refs in data.items()
        )
        self.data = data
        self._update_message()
        if signature == self._signature:
            return
        self._signature = signature
        if same_order:
            refs = self.data.get(self._card, [])
            self._references = {ref.identifier: ref for ref in refs}
            for ref in refs:
                if self.references.exists(ref.identifier):
                    self.references.item(ref.identifier, values=(ref.name, ref.identifier, ref.hits))
            self.set_disabled(self._disabled)
            return
        self.render_cards()

    def refresh_order(self):
        self._allow_reorder = True
        self._refresh()

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
        if cards:
            selected = self._card if self._card in cards else cards[0]
            self.cards.selection_set(selected)
            self.cards.focus(selected)
            self._select_card()
        else:
            self._card = None
            self._clear_references("没有匹配的卡牌")
            self.set_disabled(self._disabled)

    def _clear_references(self, text):
        self._references = {}
        self.references.delete(*self.references.get_children())
        self.title.set(text)

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
        for ref in refs:
            self._references[ref.identifier] = ref
            self.references.insert("", "end", iid=ref.identifier, values=(ref.name, ref.identifier, ref.hits))
        if refs:
            selected = tuple(key for key in previous if key in self._references) or (refs[0].identifier,)
            self.references.selection_set(selected)
        self.set_disabled(self._disabled)

    def _update_message(self):
        cleared = any(ref.cleared for refs in self.data.values() for ref in refs)
        self.message.set(
            "本次上传将同步清空结果及清空后新增的命中数。"
            if cleared and self.update_hits.get()
            else "命中数仅在本地清空；未勾选更新命中数，上传不包含统计。"
            if cleared
            else "本次上传包含命中统计；清空本地命中数作用于全部卡牌。"
            if self.update_hits.get()
            else "首选由你设置，其余按命中数排序；命中数代表使用频率。上传默认不包含统计。"
        )

    def set_disabled(self, disabled):
        self._disabled = disabled
        for button in (self.preferred_button, self.upload_button):
            button.configure(state="disabled" if disabled or not self._references else "normal")
        self.clear_button.configure(state="disabled" if disabled else "normal")
        self.hits_check.configure(state="disabled" if disabled else "normal")
