"""Compact per-fingerprint rows; all widgets and photos stay on Tk's thread."""

import ttkbootstrap as ttk
from PIL import ImageTk
from ttkbootstrap.widgets.scrolled import ScrolledFrame


class CardReferenceRows(ttk.Frame):
    def __init__(self, parent, *, rename, delete):
        super().__init__(parent)
        self._rename, self._delete = rename, delete
        self.rows = {}
        self._signature = None
        self._photos = {}
        self.scroller = ScrolledFrame(self, padding=8, autohide=False)
        self.scroller.pack(fill="both", expand=True)
        self.scroller.columnconfigure(1, weight=1)
        self.bind("<<ThemeChanged>>", self._update_theme, add="+")
        self._update_theme()

    def _update_theme(self, _event=None):
        style = ttk.Style()
        style_name = "ReferenceAction.secondary.Outline.TButton"
        style.configure(style_name, foreground="#ffffff")
        style.map(style_name, foreground=[("disabled", "#ffffff"), ("active", "#ffffff"), ("!disabled", "#ffffff")])

    def render(self, references):
        signature = tuple(
            (
                identifier,
                tuple(sample.key for sample in sources),
                sources[0].card_id,
                repr(sources[0].review.get("library_runtime", {})),
                sources[0].review.get("formal_status"),
            )
            for identifier, sources in references.items()
        )
        if signature == self._signature:
            return False
        self._signature = signature
        self._photos.clear()
        self.rows.clear()
        for widget in self.scroller.winfo_children():
            widget.destroy()
        if not references:
            ttk.Label(self.scroller, text="选择上方卡牌，查看每份参考样本及其补足记录。").grid(
                row=0, column=0, sticky="w"
            )
        for index, (identifier, sources) in enumerate(references.items()):
            sample = sources[0]
            stats = sample.review.get("library_runtime", {})
            image = ttk.Label(self.scroller, text="无图", width=5, anchor="center")
            image.grid(row=index, column=0, sticky="ns", padx=(0, 10), pady=4)
            text = (
                f"参考 {index + 1}  ·  补足识别 {stats.get('assists', 0)} 次  ·  {stats.get('assist_battles', 0)} 个对局"
                f"  ·  {sample.review.get('formal_status', '暂不建议')}\n{sample.key}"
            )
            label = ttk.Label(self.scroller, text=text, justify="left", wraplength=320)
            label.grid(row=index, column=1, sticky="w", pady=4)
            actions = ttk.Frame(self.scroller)
            actions.grid(row=index, column=2, sticky="e", padx=(8, 0), pady=4)
            buttons = {}
            for action, title, callback in (
                ("rename", "修改卡名", self._rename),
                ("delete", "删除", self._delete),
            ):
                button = ttk.Button(
                    actions,
                    text=title,
                    command=lambda key=identifier, cb=callback: cb(key),
                    style="ReferenceAction.secondary.Outline.TButton",
                )
                button.pack(side="left", padx=(0, 4))
                buttons[action] = button
            self.rows[identifier] = {"sample": sample, "image": image, "label": label, **buttons}
        return True

    def set_running(self, running, busy):
        for row in self.rows.values():
            for key in ("rename", "delete"):
                row[key].configure(state="disabled" if busy or key == "delete" and running else "normal")

    def set_image(self, identifier, preview):
        if identifier in self.rows:
            photo = ImageTk.PhotoImage(preview, master=self) if preview is not None else None
            self._photos[identifier] = photo
            self.rows[identifier]["image"].configure(image=photo or "", text="" if photo else "无图")
