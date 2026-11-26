"""Text-only reference catalog with explicit first-choice and statistics controls."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, replace
from tkinter.font import Font

import ttkbootstrap as ttk
from PIL import Image, ImageDraw, ImageFont, ImageTk

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
        self._sort_by_recent = True
        self._running = False
        self._card = None
        self._checked_cards = set()
        self._check_images = {}
        self._card_images = {}
        self._new_image = ""
        self._deleted_image = ""
        self._both_image = ""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        # Keep the viewport steady when drag feedback wraps to a different number of lines.
        self.rowconfigure(2, minsize=Font(root=self, font="TkDefaultFont").metrics("linespace") * 3)
        search = ttk.Frame(self)
        search.grid(row=0, column=0, sticky="ew", pady=(12, 10))
        ttk.Label(search, text="搜索卡名 / 英文 ID").pack(side="left")
        self.query = tk.StringVar()
        ttk.Entry(search, textvariable=self.query, width=24).pack(side="left", padx=6)
        self.upload_button = ttk.Button(search, text="上传所选修改", command=lambda: action("upload"))
        self.upload_button.pack(side="right", padx=(0, 6))
        self.select_modified_button = ttk.Button(
            search, text="选择所有修改项", command=lambda: action("select_modified")
        )
        self.select_modified_button.pack(side="right", padx=(0, 8))
        self.query.trace_add("write", lambda *_: self.render_cards())
        self.panes = tk.PanedWindow(self, orient="horizontal", sashwidth=6, opaqueresize=True, borderwidth=0)
        self.panes.grid(row=1, column=0, sticky="nsew")
        left = ttk.Frame(self.panes)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.panes.add(left, minsize=180, width=270, stretch="never")
        self.cards = ttk.Treeview(
            left,
            columns=("card", "count"),
            show="tree headings",
            selectmode="none",
            style="CatalogCards.CardSamples.Treeview",
        )
        self.cards.heading("#0", text="全选", command=self._check_all_cards)
        check_width = Font(root=self, font="TkDefaultFont").measure("全选") + 46
        self.cards.column("#0", width=check_width, minwidth=check_width, stretch=False, anchor="center")
        self.cards.heading("card", text="卡名", command=self._sort_names)
        self.cards.column("card", width=90, minwidth=80)
        self.cards.heading("count", text="样本数", command=self._sort_counts)
        self.cards.column("count", width=60, minwidth=54, stretch=False)
        self.cards.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.cards.yview)
        scroll.grid(row=0, column=1, sticky="ns")

        self.cards.configure(yscrollcommand=scroll.set)
        self.cards.bind("<<TreeviewSelect>>", self._select_card)
        self.cards.bind("<Button-1>", self._check_card)
        self.cards.bind("<space>", self._check_card)
        card_tools = ttk.Labelframe(self, text="维护操作 · 左侧勾选卡牌", padding=(10, 8))
        card_tools.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        self.clear_cards_button = ttk.Button(
            card_tools, text="清空次数", bootstyle="secondary", command=lambda: action("clear_cards")
        )
        self.clear_cards_button.grid(row=0, column=0, sticky="w")
        self.reset_cards_button = ttk.Button(
            card_tools, text="重置为远端", bootstyle="secondary", command=lambda: action("reset_cards")
        )
        self.reset_cards_button.grid(row=0, column=1, sticky="w", padx=(12, 0))
        right = ttk.Frame(self.panes)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        self.panes.add(right, minsize=220, stretch="always")
        self.title = tk.StringVar(value="选择卡牌查看正式参考")
        ttk.Label(right, textvariable=self.title).grid(row=0, column=0, sticky="ew", padx=6, pady=4)
        tools = ttk.Frame(right)
        tools.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 4))
        self.clear_button = ttk.Button(
            tools, text="清空所选样本本机命中次数", bootstyle="secondary", command=lambda: action("clear")
        )
        self.clear_button.pack(side="left")
        gallery = ttk.Frame(right)
        gallery.columnconfigure(0, weight=1)
        gallery.rowconfigure(0, weight=1)
        self.references = ttk.Treeview(
            gallery,
            columns=("sort", "name", "identifier", "hits"),
            show="tree headings",
            height=4,
            selectmode="extended",
            style="CardReferences.Treeview",
        )
        self.references.heading("sort", text="排序")
        self.references.column("#0", width=60, minwidth=60, stretch=False, anchor="center")
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
        reference_tools = ttk.Frame(right)
        reference_tools.grid(row=3, column=0, sticky="ew", padx=6, pady=(8, 0))
        self.delete_button = ttk.Button(
            reference_tools,
            text="删除参考",
            bootstyle="danger",
            state="disabled",
            command=lambda: action("delete_references"),
        )
        self.delete_button.pack(side="right")
        self.restore_button = ttk.Button(
            reference_tools,
            text="还原参考",
            bootstyle="secondary",
            state="disabled",
            command=lambda: action("restore_references"),
        )
        self.restore_button.pack(side="right", padx=(0, 8))
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
        self.bind("<<ThemeChanged>>", self._update_check_style, add="+")
        self._update_check_style()

    def _update_check_style(self, _event=None):
        style = ttk.Style()
        size = max(16, Font(root=self, font="TkDefaultFont").metrics("linespace"))
        style.configure("CatalogCards.CardSamples.Treeview", rowheight=max(34, size + 14))
        self.cards.configure(style="CatalogCards.CardSamples.Treeview")
        self.cards.tag_configure("viewing", background=style.colors.secondary, foreground=style.colors.selectfg)
        style.layout(
            "CatalogCards.CardSamples.Treeview.Item",
            [
                (
                    "Treeitem.padding",
                    {"sticky": "nsew", "children": [("Treeitem.image", {"sticky": ""})]},
                )
            ],
        )
        style.configure("CatalogReferences.CardReferences.Treeview", rowheight=max(34, size + 14))
        self.references.configure(style="CatalogReferences.CardReferences.Treeview")
        style.layout(
            "CatalogReferences.CardReferences.Treeview.Item",
            style.layout("CatalogCards.CardSamples.Treeview.Item"),
        )
        font = ImageFont.load_default(size=size * 2)
        badge_width = int(font.getlength("new")) + 24
        badge = Image.new("RGBA", (badge_width, size * 3))
        draw_badge = ImageDraw.Draw(badge)
        draw_badge.rounded_rectangle((0, 3, badge_width - 1, size * 3 - 4), radius=9, fill="#fbbf24")
        draw_badge.text((12, size * 1.5), "new", font=font, fill="#1f2937", anchor="lm")
        self._new_image = ImageTk.PhotoImage(
            badge.resize((badge_width // 3, size), Image.Resampling.LANCZOS), master=self
        )
        deleted_width = int(font.getlength("delete")) + 24
        deleted_badge = Image.new("RGBA", (deleted_width, size * 3))
        deleted_draw = ImageDraw.Draw(deleted_badge)
        deleted_draw.rounded_rectangle((0, 3, deleted_width - 1, size * 3 - 4), radius=9, fill="#fb7185")
        deleted_draw.text((12, size * 1.5), "delete", font=font, fill="#1f2937", anchor="lm")
        self._deleted_image = ImageTk.PhotoImage(
            deleted_badge.resize((deleted_width // 3, size), Image.Resampling.LANCZOS), master=self
        )
        both_badges = Image.new("RGBA", (badge_width + 12 + deleted_width, size * 3))
        both_badges.alpha_composite(badge)
        both_badges.alpha_composite(deleted_badge, (badge_width + 12, 0))
        self._both_image = ImageTk.PhotoImage(
            both_badges.resize((both_badges.width // 3, size), Image.Resampling.LANCZOS), master=self
        )
        self.references.column("#0", width=self._both_image.width() + 12, minwidth=self._both_image.width() + 12)
        checkbox_width = size + 4 + self._both_image.width() + 8
        self.cards.column("#0", width=checkbox_width, minwidth=checkbox_width)
        for checked in (False, True, None):
            for disabled in (False, True):
                color = style.colors.secondary if disabled else style.colors.primary
                border = style.colors.border if disabled else style.colors.fg
                picture = Image.new("RGBA", (size * 3, size * 3))
                draw = ImageDraw.Draw(picture)
                draw.rounded_rectangle(
                    (3, 3, size * 3 - 4, size * 3 - 4),
                    radius=9,
                    fill=color if checked is not False else style.colors.inputbg,
                    outline=color if checked is not False else border,
                    width=3,
                )
                if checked is True:
                    draw.line(
                        [(size * 0.7, size * 1.5), (size * 1.3, size * 2.1), (size * 2.3, size * 0.9)],
                        fill=style.colors.selectfg,
                        width=5,
                        joint="curve",
                    )
                elif checked is None:
                    draw.line((size * 0.8, size * 1.5, size * 2.2, size * 1.5), fill=style.colors.selectfg, width=5)
                self._check_images[checked, disabled] = ImageTk.PhotoImage(
                    picture.resize((size, size), Image.Resampling.LANCZOS), master=self
                )
                for changed in (False, True, "delete", "both"):
                    combined = Image.new("RGBA", (size * 3 + 12 + both_badges.width, size * 3))
                    combined.alpha_composite(picture)
                    if changed:
                        marker = {"delete": deleted_badge, "both": both_badges}.get(changed, badge)
                        combined.alpha_composite(marker, (size * 3 + 12, 0))
                    self._card_images[checked, disabled, changed] = ImageTk.PhotoImage(
                        combined.resize((combined.width // 3, size), Image.Resampling.LANCZOS), master=self
                    )
        self._refresh_checks()
        self._refresh_reference_rows()

    def set_data(self, data):
        if self._drag is not None or self._pending_order is not None:
            return
        same_order = tuple((card, tuple(ref.identifier for ref in refs)) for card, refs in data.items()) == tuple(
            (card, tuple(ref.identifier for ref in refs)) for card, refs in self.data.items()
        )
        usage_changed = {ref.identifier: ref.last_used for refs in data.values() for ref in refs} != {
            ref.identifier: ref.last_used for refs in self.data.values() for ref in refs
        }
        signature = tuple(
            (
                card,
                tuple(
                    (
                        ref.identifier,
                        ref.uploaded,
                        ref.deletable,
                        ref.deleted,
                        ref.reference_changed,
                        ref.name,
                        ref.hits,
                        ref.order_changed,
                        ref.hits_changed,
                        ref.last_used,
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
            if not self._running or usage_changed:
                self._prioritize_changed_cards()
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
        self._sort_by_recent = False
        self.render_cards()

    def set_running(self, running):
        self._running = running
        if not running:
            self._prioritize_changed_cards()

    def _card_name(self, card):
        return CARD_NAMES_ZH.get(card, card)

    def render_cards(self):
        self._cancel_drag()
        query = "".join(self.query.get().casefold().split())
        cards = [card for card in self.data if query in f"{CARD_NAMES_ZH.get(card, card)}{card}".casefold()]
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
        self._prioritize_changed_cards()
        if cards:
            selected = self._card if self._card in cards else self.cards.get_children()[0]
            self.cards.focus(selected)
            self._select_card()
        else:
            self._card = None
            self._clear_references("没有匹配的卡牌")
            self.set_disabled(self._disabled)

    def _prioritize_changed_cards(self):
        cards = sorted(
            self.cards.get_children(),
            key=lambda card: (
                -len(self.data[card]) if self._sort_count else len(self.data[card]),
                CARD_NAMES_ZH.get(card, card),
            )
            if self._sort_count is not None
            else (
                -max((ref.last_used for ref in self.data[card]), default=0) if self._sort_by_recent else 0,
                CARD_NAMES_ZH.get(card, card),
            ),
        )
        cards.sort(key=lambda card: not any(ref.deleted or ref.changed for ref in self.data[card]))
        for index, card in enumerate(cards):
            self.cards.move(card, "", index)

    def _clear_references(self, text):
        self._references = {}
        self.references.delete(*self.references.get_children())
        self.title.set(text)

    def _select_card(self, _event=None):
        selection = self.cards.selection()
        card = self.cards.focus() or (selection[0] if selection else None)
        if card is None:
            self.set_disabled(self._disabled)
            return
        if self._drag is not None and card == self._card:
            return
        if card != self._card:
            self._cancel_drag()
        previous = self.references.selection() if card == self._card else ()
        self._card = card
        refs = self.data.get(card, [])
        self._clear_references("暂无正式参考" if not refs else "请选择参考")
        deleted_count = sum(ref.deleted for ref in refs)
        title = f"{CARD_NAMES_ZH.get(card, card)} · {len(refs) - deleted_count} 份正式参考"
        if deleted_count:
            title += f" · {deleted_count} 份已删除，可还原"
        self.title.set(title)
        for index, ref in enumerate(refs, 1):
            self._references[ref.identifier] = ref
            self.references.insert(
                "",
                "end",
                iid=ref.identifier,
                values=("" if ref.deleted else f"≡ {index}", ref.name, ref.identifier, ref.hits),
                image=self._reference_image(ref),
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
            "按住排序列的 ≡ 上下拖动，第一行是首选。琥珀色 new 标签表示顺序或命中次数有变化。"
            "勾选卡牌后可清空次数或重置为远端。清空后线上数据保留，上传所选清空记录后同步线上。"
            "delete 表示本机已删除，可选中后还原。"
        )

    def _card_values(self, card):
        return self._card_name(card), sum(not ref.deleted for ref in self.data[card])

    def _check_card(self, event):
        if event.keysym == "space":
            card = self.cards.focus()
            toggle = True
        elif self.cards.identify_region(event.x, event.y) in ("tree", "cell"):
            card = self.cards.identify_row(event.y)
            toggle = self.cards.identify_region(event.x, event.y) == "tree"
        else:
            return
        if not card:
            return "break"
        self.cards.focus(card)
        if toggle and not self._disabled and self.data.get(card):
            self._checked_cards.symmetric_difference_update((card,))
        self._select_card()
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

    def clear_uploaded_cards(self, cards):
        self._checked_cards.difference_update(cards)
        self._refresh_checks()

    def modified_cards(self):
        return tuple(card for card, refs in self.data.items() if any(ref.changed for ref in refs))

    def locate_card(self, card):
        if card in self.data:
            self._card = card
            self.query.set("")
            self.cards.see(card)

    def select_cards(self, cards):
        selected = tuple(card for card in cards if card in self.data)
        self._checked_cards = set(selected)
        if selected:
            self._card = selected[0]
        self.query.set("")
        self._refresh_checks()
        if selected:
            self.cards.see(selected[0])

    def check_modified_cards(self):
        cards = self.modified_cards()
        if cards:
            self.select_cards(cards)
        return cards

    def selected_card_references(self):
        return tuple(ref.identifier for card in self.selected_cards() for ref in self.data[card] if not ref.deleted)

    def selected_deletable_references(self):
        refs = tuple(self._references.get(key) for key in self.references.selection())
        if any(ref is None or not ref.deletable for ref in refs):
            return ()
        return refs

    def selected_deleted_references(self):
        refs = tuple(self._references.get(key) for key in self.references.selection())
        return refs if refs and all(ref is not None and ref.deleted for ref in refs) else ()

    def _reference_image(self, ref):
        if ref.deleted:
            return self._both_image if ref.order_changed or ref.hits_changed else self._deleted_image
        if ref.changed:
            return self._new_image
        return ""

    def set_disabled(self, disabled):
        self._disabled = disabled
        visible = {card for card in self.cards.get_children() if self.data[card]}
        all_checked = bool(visible) and visible.issubset(self._checked_cards)
        checked_cards = self.selected_cards()
        if set(self.cards.selection()) != set(checked_cards):
            self.cards.selection_set(checked_cards)
        header_state = True if all_checked else None if visible & self._checked_cards else False
        self.cards.heading("#0", text="全选", image=self._check_images[header_state, disabled])
        for card in self.cards.get_children():
            has_new = any(
                ref.order_changed or ref.hits_changed or (ref.reference_changed and not ref.deleted)
                for ref in self.data[card]
            )
            has_deleted = any(ref.deleted for ref in self.data[card])
            changed = "both" if has_new and has_deleted else "delete" if has_deleted else has_new
            self.cards.item(
                card,
                image=self._card_images[card in self._checked_cards, disabled or not self.data[card], changed],
                tags=("viewing",) if card == self._card else (),
            )
        if disabled:
            self._cancel_drag()
        checked = self.selected_card_references()
        self.upload_button.configure(state="disabled" if disabled or not checked_cards else "normal")
        self.select_modified_button.configure(state="disabled" if disabled or not self.modified_cards() else "normal")
        selected_active = any(
            key in self._references and not self._references[key].deleted for key in self.references.selection()
        )
        self.clear_button.configure(state="disabled" if disabled or not selected_active else "normal")
        self.delete_button.configure(
            state="disabled" if disabled or not self.selected_deletable_references() else "normal"
        )
        self.restore_button.configure(
            state="disabled" if disabled or not self.selected_deleted_references() else "normal"
        )
        self.clear_cards_button.configure(state="disabled" if disabled or not checked else "normal")
        self.reset_cards_button.configure(state="disabled" if disabled or self._refreshing or not checked else "normal")

    def _refresh_reference_rows(self, start=0, stop=None):
        for index, key in enumerate(self.references.get_children()[start:stop], start + 1):
            ref = self._references[key]
            self.references.item(
                key,
                values=("" if ref.deleted else f"≡ {index}", ref.name, key, ref.hits),
                image=self._reference_image(ref),
            )

    def _is_handle(self, event):
        return (
            self.references.identify_region(event.x, event.y) == "cell"
            and self.references.identify_column(event.x) == "#1"
            and bool(self.references.identify_row(event.y))
            and not self._references[self.references.identify_row(event.y)].deleted
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
        if target and target != drag.identifier and not self._references[target].deleted:
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
            self._reorder(
                drag.card,
                tuple(key for key in ordered if not refs[key].deleted),
                tuple(key for key in drag.original if not refs[key].deleted),
            )
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
