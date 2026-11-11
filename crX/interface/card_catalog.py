"""Text-only reference catalog with explicit first-choice and statistics controls."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, replace
from tkinter.font import Font

import ttkbootstrap as ttk

from crX.bot.card_data import CARD_NAMES_ZH


@dataclass
class ReferenceDrag:
    card: str
    identifier: str
    original: tuple[str, ...]
    start_y: int
    moved: bool = False


class CardCatalogView(ttk.Frame):
    def __init__(self, parent, *, refresh, action=lambda _operation: None, reorder=lambda *_args: None, status=None):
        super().__init__(parent)
        self.data = {}
        self._action = action
        self._reorder = reorder
        self._drag = None
        self._disabled = False
        self._pending_order = None
        self._scroll_after = None
        self._drag_y = 0
        self._refresh = refresh
        self._signature = None
        self._references = {}
        self._sort_count = None
        self._card = None
        self._checking_cards = False
        self._checked_cards = set()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        # Keep the viewport steady when drag feedback wraps to a different number of lines.
        self.rowconfigure(2, minsize=Font(root=self, font="TkDefaultFont").metrics("linespace") * 3)
        search = ttk.Frame(self)
        search.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(search, text="搜索卡名 / 英文 ID").pack(side="left")
        self.query = tk.StringVar()
        ttk.Entry(search, textvariable=self.query, width=24).pack(side="left", padx=6)
        self.only_samples = tk.BooleanVar(value=False)
        ttk.Checkbutton(search, text="只看有样本", variable=self.only_samples, command=self.render_cards).pack(
            side="left"
        )
        self.upload_button = ttk.Button(search, text="上传修改", command=lambda: action("upload"))
        self.upload_button.pack(side="right", padx=(0, 6))
        self.query.trace_add("write", lambda *_: self.render_cards())
        self.panes = tk.PanedWindow(self, orient="horizontal", sashwidth=6, opaqueresize=True, borderwidth=0)
        self.panes.grid(row=1, column=0, sticky="nsew")
        left = ttk.Frame(self.panes)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.panes.add(left, minsize=180, width=270, stretch="never")
        self.cards = ttk.Treeview(
            left,
            columns=("checked", "card", "count"),
            displaycolumns=("card", "count"),
            show="headings",
            selectmode="browse",
            style="CardSamples.Treeview",
        )
        self.cards.heading("checked", text="☐ 全选", command=self._check_all_cards)
        check_width = Font(root=self, font="TkDefaultFont").measure("☑ 取消全选") + 20
        self.cards.column("checked", width=check_width, minwidth=check_width, stretch=False, anchor="center")
        self.cards.heading("card", text="卡名", command=self._sort_names)
        self.cards.column("card", width=150, minwidth=80)
        self.cards.heading("count", text="样本数", command=self._sort_counts)
        self.cards.column("count", width=60, minwidth=54, stretch=False)
        self.cards.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.cards.yview)
        scroll.grid(row=0, column=1, sticky="ns")

        self.cards.configure(yscrollcommand=scroll.set)
        self.cards.bind("<<TreeviewSelect>>", self._select_card)
        self.cards.bind("<Button-1>", self._check_card)
        self.cards.bind("<space>", self._check_card)
        card_tools = ttk.Frame(left)
        card_tools.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.clear_cards_button = ttk.Button(card_tools, text="清空次数", command=self._clear_cards)
        self.clear_cards_button.pack(side="left")
        self.reset_cards_button = ttk.Button(card_tools, text="重置为远端", command=self._reset_cards)
        self.reset_cards_button.pack(side="left", padx=(4, 0))
        self.cancel_checks_button = ttk.Button(
            card_tools, text="退出勾选", bootstyle="light-link", command=self._cancel_checks
        )
        right = ttk.Frame(self.panes)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        self.panes.add(right, minsize=220, stretch="always")
        self.title = tk.StringVar(value="选择卡牌查看正式参考")
        ttk.Label(right, textvariable=self.title).grid(row=0, column=0, sticky="ew", padx=6, pady=4)
        tools = ttk.Frame(right)
        tools.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 4))
        self.clear_button = ttk.Button(
            tools, text="清空所选样本本机命中次数", bootstyle="secondary-outline", command=lambda: action("clear")
        )
        self.clear_button.pack(side="left")
        gallery = ttk.Frame(right)
        gallery.columnconfigure(0, weight=1)
        gallery.rowconfigure(0, weight=1)
        self.references = ttk.Treeview(
            gallery,
            columns=("sort", "name", "identifier", "hits"),
            show="headings",
            height=4,
            selectmode="extended",
            style="CardReferences.Treeview",
        )
        self.references.heading("sort", text="排序")
        self.references.column("sort", width=58, minwidth=58, stretch=False, anchor="center")
        self.references.heading("name", text="参考")
        self.references.column("name", width=120, minwidth=60)
        self.references.heading("identifier", text="参考编号")
        self.references.column("identifier", width=160, minwidth=100)
        self.references.heading("hits", text="命中数")
        self.references.column("hits", width=80, minwidth=60, stretch=False, anchor="e")
        self.references.grid(row=0, column=0, sticky="nsew")
        self.references.bind("<<TreeviewSelect>>", lambda _event: self.set_disabled(self._disabled))
        self.references.bind("<Motion>", self._hover_handle, add="+")
        self.references.bind("<Leave>", self._leave_handle, add="+")
        self.references.bind("<Unmap>", self._cancel_drag, add="+")
        self.references.bind("<Destroy>", self._stop_scroll, add="+")
        self.references.bind("<ButtonPress-1>", self._begin_drag, add="+")
        self.references.bind("<B1-Motion>", self._drag_reference, add="+")
        self.references.bind("<ButtonRelease-1>", self._end_drag, add="+")
        self.references.bind("<Escape>", self._cancel_drag, add="+")
        reference_scroll = ttk.Scrollbar(gallery, orient="vertical", command=self.references.yview)
        reference_scroll.grid(row=0, column=1, sticky="ns")

        self.references.configure(yscrollcommand=reference_scroll.set)
        reference_horizontal = ttk.Scrollbar(gallery, orient="horizontal", command=self.references.xview)
        reference_horizontal.grid(row=1, column=0, sticky="ew")
        self.references.configure(xscrollcommand=reference_horizontal.set)
        gallery.grid(row=2, column=0, sticky="nsew", padx=(6, 0))
        self.message = tk.StringVar()
        self._update_message()
        ttk.Label(self, textvariable=self.message, wraplength=680).grid(row=2, column=0, sticky="ew", pady=(6, 0))
        if status is not None:
            ttk.Label(self, textvariable=status, wraplength=680).grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self.refresh_feedback = ttk.Frame(self)
        self.refresh_progress = ttk.Progressbar(self.refresh_feedback, length=72, mode="indeterminate")
        self.refresh_message = tk.StringVar()
        self.refresh_label = ttk.Label(self.refresh_feedback, textvariable=self.refresh_message, wraplength=660)
        self.refresh_label.pack(side="left")
        self._refreshing = False
        self.set_disabled(False)

    def set_data(self, data):
        if self._drag is not None or self._pending_order is not None:
            return
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
                        ref.order_changed,
                        ref.hits_changed,
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
            for card in self.cards.get_children():
                self.cards.item(card, values=self._card_values(card))
            self._references = {ref.identifier: ref for ref in self.data.get(self._card, [])}
            self._refresh_reference_rows()
            self.set_disabled(self._disabled)
            return
        self.render_cards()

    def refresh_order(self):
        self._refresh()

    def start_refresh(self, message):
        self._refreshing = True
        self._cancel_drag()
        self.refresh_message.set(message)
        self.refresh_progress.pack(side="left", before=self.refresh_label, padx=(0, 8))
        self.refresh_feedback.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        self.refresh_progress.start()
        self.set_disabled(self._disabled)

    def finish_refresh(self, message):
        self._refreshing = False
        self.refresh_progress.stop()
        self.refresh_progress.pack_forget()
        self.refresh_message.set(message)
        self.set_disabled(self._disabled)

    def describe_changes(self, data):
        changed = []
        for card in sorted(self.data.keys() | data.keys(), key=lambda card: (CARD_NAMES_ZH.get(card, card), card)):
            before = {ref.identifier: ref for ref in self.data.get(card, ())}
            after = {ref.identifier: ref for ref in data.get(card, ())}
            common = before.keys() & after.keys()
            details = []
            if before.keys() != after.keys():
                details.append("参考增减")
            elif tuple(before) != tuple(after) or any(before[key].name != after[key].name for key in common):
                details.append("参考顺序")
            if any(before[key].hits != after[key].hits for key in common):
                details.append("命中次数")
            if any(before[key].uploaded != after[key].uploaded for key in common):
                details.append("上传状态")
            if details:
                changed.append(f"{CARD_NAMES_ZH.get(card, card)}（{'、'.join(details)}）")
        if not changed:
            return "样本库无变化"
        return f"样本库变更 {len(changed)} 种卡牌：{'、'.join(changed[:3])}{'等' if len(changed) > 3 else ''}"

    def update_hits(self, counts, changed):
        self.set_data(
            {
                card: [
                    replace(ref, hits=counts.get(ref.identifier, 0), hits_changed=ref.identifier in changed)
                    for ref in refs
                ]
                for card, refs in self.data.items()
            }
        )

    def _sort_counts(self):
        self._sort_count = not self._sort_count if self._sort_count is not None else True
        self.render_cards()

    def _sort_names(self):
        self._sort_count = None
        self.render_cards()

    def _card_name(self, card):
        refs = self.data[card]
        return (
            CARD_NAMES_ZH.get(card, card)
            + (" ↕" if any(ref.order_changed for ref in refs) else "")
            + (" ●" if any(ref.hits_changed for ref in refs) else "")
        )

    def render_cards(self):
        self._cancel_drag()
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
        self._checked_cards.intersection_update(cards)
        self.cards.delete(*self.cards.get_children())
        for card in cards:
            self.cards.insert(
                "",
                "end",
                iid=card,
                text="",
                values=self._card_values(card),
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
            self.set_disabled(self._disabled)
            return
        focused = self.cards.focus()
        card = focused if focused in selection else selection[0]
        if self._drag is not None and card == self._card:
            return
        if card != self._card:
            self._cancel_drag()
        previous = self.references.selection() if card == self._card else ()
        self._card = card
        refs = self.data.get(card, [])
        self._clear_references("暂无正式参考" if not refs else "请选择参考")
        self.title.set(f"{CARD_NAMES_ZH.get(card, card)} · {len(refs)} 份正式参考")
        for index, ref in enumerate(refs, 1):
            self._references[ref.identifier] = ref
            self.references.insert(
                "", "end", iid=ref.identifier, values=(f"≡ {index}", ref.display_name, ref.identifier, ref.hits)
            )
        if refs:
            selected = tuple(key for key in previous if key in self._references) or (refs[0].identifier,)
            self.references.selection_set(selected)
        self.set_disabled(self._disabled)

    @property
    def summary(self):
        refs = [ref for references in self.data.values() for ref in references]
        return f"顺序变化：{sum(ref.order_changed for ref in refs)} 个样本 · 命中变化：{sum(ref.hits_changed for ref in refs)} 个样本"

    def _update_message(self):
        self.message.set(
            "按住排序列的 ≡ 上下拖动，第一行是首选。↕ 顺序变化，● 命中变化。"
            "勾选卡牌可批量清空本机显示的次数。保留勾选并上传修改可清空线上对应次数。"
            if self._checking_cards
            else "按住排序列的 ≡ 上下拖动，第一行是首选。↕ 顺序变化，● 命中变化。清空只影响本机贡献，线上数据保留。"
        )

    def _card_values(self, card):
        checked = "☑" if card in self._checked_cards else "☐"
        return checked, self._card_name(card), len(self.data[card])

    def _clear_cards(self):
        if self._checking_cards:
            self._action("clear_cards")
        else:
            self._begin_checks()

    def _reset_cards(self):
        if self._checking_cards:
            self._action("reset_cards")
        else:
            self._begin_checks()

    def _begin_checks(self):
        self._checking_cards = True
        self.cards.configure(displaycolumns=("checked", "card", "count"))
        self.cancel_checks_button.pack(side="left")
        self._update_message()
        self.set_disabled(self._disabled)

    def _cancel_checks(self):
        self._checking_cards = False
        self._checked_cards.clear()
        self.cards.configure(displaycolumns=("card", "count"))
        self.cancel_checks_button.pack_forget()
        self._refresh_checks()
        self._update_message()

    def _check_card(self, event):
        if not self._checking_cards or self._disabled:
            return
        if event.keysym == "space":
            card = self.cards.focus()
        elif self.cards.identify_region(event.x, event.y) == "cell" and self.cards.identify_column(event.x) == "#1":
            card = self.cards.identify_row(event.y)
        else:
            return
        if card and self.data.get(card):
            self._checked_cards.symmetric_difference_update((card,))
            self._refresh_checks()
        return "break"

    def _check_all_cards(self):
        if self._disabled:
            return
        visible = {card for card in self.cards.get_children() if self.data[card]}
        self._checked_cards = set() if visible == self._checked_cards else visible
        self._refresh_checks()

    def _refresh_checks(self):
        for card in self.cards.get_children():
            self.cards.item(card, values=self._card_values(card))
        self.set_disabled(self._disabled)

    def selected_cards(self):
        return tuple(card for card in self.cards.get_children() if card in self._checked_cards)

    def selected_card_references(self):
        return tuple(ref.identifier for card in self.selected_cards() for ref in self.data[card])

    def set_disabled(self, disabled):
        self._disabled = disabled
        visible = {card for card in self.cards.get_children() if self.data[card]}
        all_checked = bool(visible) and visible.issubset(self._checked_cards)
        self.cards.heading("checked", text="☑ 取消全选" if all_checked else "☐ 全选")
        if disabled:
            self._cancel_drag()
        checked = self.selected_card_references()
        self.upload_button.configure(state="disabled" if disabled or self._checking_cards and not checked else "normal")
        self.clear_button.configure(state="disabled" if disabled or not self.references.selection() else "normal")
        self.clear_cards_button.configure(
            state="disabled" if disabled or self._checking_cards and not checked else "normal"
        )
        self.reset_cards_button.configure(
            state="disabled" if disabled or self._refreshing or self._checking_cards and not checked else "normal"
        )
        self.cancel_checks_button.configure(state="disabled" if disabled else "normal")

    def _refresh_reference_rows(self, start=0, stop=None):
        for index, key in enumerate(self.references.get_children()[start:stop], start + 1):
            ref = self._references[key]
            self.references.item(key, values=(f"≡ {index}", ref.display_name, key, ref.hits))

    def _is_handle(self, event):
        return (
            self.references.identify_region(event.x, event.y) == "cell"
            and self.references.identify_column(event.x) == "#1"
            and bool(self.references.identify_row(event.y))
        )

    def _hover_handle(self, event):
        if self._drag is None:
            self.references.configure(cursor="hand2" if not self._disabled and self._is_handle(event) else "")

    def _leave_handle(self, _event=None):
        if self._drag is None:
            self.references.configure(cursor="")

    def _begin_drag(self, event):
        self._cancel_drag()
        if (
            self._disabled
            or self._refreshing
            or self._pending_order is not None
            or event.state & 0x0005
            or not self._is_handle(event)
        ):
            return
        identifier = self.references.identify_row(event.y)
        if self._card is None:
            return
        self._drag = ReferenceDrag(self._card, identifier, self.references.get_children(), event.y)
        self._drag_y = event.y
        self.references.selection_set(identifier)
        self.references.focus(identifier)
        self.references.focus_set()
        self.references.configure(cursor="fleur")
        self.message.set("上下拖动调整位置，松手保存；按 Esc 取消。第一行自动成为首选。")
        # Prevent Treeview's class bindings from extending selection while dragging.
        return "break"

    def _drag_reference(self, event):
        drag = self._drag
        if drag is None or self._disabled:
            return
        if drag.card != self._card:
            self._cancel_drag()
            return
        self._drag_y = event.y
        if not drag.moved and abs(event.y - drag.start_y) < 3:
            return "break"
        drag.moved = True
        self._move_dragged_row()
        if self._scroll_after is None:
            self._scroll_after = self.after(80, self._scroll_drag)
        return "break"

    def _move_dragged_row(self):
        drag = self._drag
        if drag is None:
            return
        target = self.references.identify_row(self._drag_y)
        rows = self.references.get_children()
        if not target:
            visible = [key for key in rows if self.references.bbox(key)]
            if visible and self._drag_y < 28:
                target = visible[0]
            elif visible and self._drag_y > self.references.winfo_height() - 20:
                target = visible[-1]
        if target and target != drag.identifier:
            box = self.references.bbox(target)
            source_index, target_index = self.references.index(drag.identifier), self.references.index(target)
            if not box or (
                self._drag_y >= box[1] + box[3] / 2
                if target_index > source_index
                else self._drag_y <= box[1] + box[3] / 2
            ):
                self.references.move(drag.identifier, "", target_index)
                self._refresh_reference_rows(min(source_index, target_index), max(source_index, target_index) + 1)
        position = self.references.index(drag.identifier) + 1
        self.message.set(f"正在拖动：第 {position} / {len(rows)} 位 · 松手保存，Esc 取消 · 第一行是首选")

    def _scroll_drag(self):
        self._scroll_after = None
        if self._drag is None or self._disabled:
            return
        # Continue scrolling at the edge even when the pointer stops moving.
        direction = -1 if self._drag_y < 28 else 1 if self._drag_y > self.references.winfo_height() - 20 else 0
        if direction:
            self.references.yview_scroll(direction, "units")
            self._move_dragged_row()
            self._scroll_after = self.after(80, self._scroll_drag)

    def _end_drag(self, _event=None):
        drag = self._drag
        if drag is None:
            return
        ordered = self.references.get_children()
        self._clear_drag()
        if drag.moved and ordered != drag.original and not self._disabled and drag.card == self._card:
            before = self.data[drag.card]
            self._pending_order = (drag.card, before)
            refs = {ref.identifier: replace(ref) for ref in before}
            first = refs[ordered[0]]
            previous = next((ref for ref in refs.values() if ref.preferred), None)
            if previous is not None and previous.identifier != first.identifier:
                previous.number = first.number
            for index, key in enumerate(ordered):
                ref = refs[key]
                ref.preferred = index == 0
                if ref.preferred:
                    ref.number = 0
                ref.order_changed |= drag.original[index] != key
            self.data[drag.card] = [refs[key] for key in ordered]
            self._references = refs
            self._refresh_reference_rows()
            self.message.set("正在保存顺序……第一行是首选。")
            self._reorder(drag.card, ordered, drag.original)
        else:
            self._update_message()
        return "break"

    def finish_reorder(self, success):
        if self._pending_order is None:
            return
        card, before = self._pending_order
        self._pending_order = None
        self._signature = None
        if not success:
            self.data[card] = before
            if self._card == card:
                self._references = {ref.identifier: ref for ref in before}
                for index, ref in enumerate(before):
                    self.references.move(ref.identifier, "", index)
                self._refresh_reference_rows()
        self.message.set("顺序已保存；第一行是首选。" if success else "顺序保存失败，已恢复原顺序；请刷新后重试。")

    def _stop_scroll(self, _event=None):
        if self._scroll_after is not None:
            self.after_cancel(self._scroll_after)
            self._scroll_after = None

    def _clear_drag(self):
        self._stop_scroll()
        self._drag = None
        self.references.configure(cursor="")

    def _cancel_drag(self, _event=None):
        drag = self._drag
        self._clear_drag()
        if drag is not None:
            if drag.card == self._card:
                for index, key in enumerate(drag.original):
                    if self.references.exists(key):
                        self.references.move(key, "", index)
                self._refresh_reference_rows()
            self._update_message()
