"""Review duplicate groups without altering source images."""

from __future__ import annotations

import threading
import tkinter as tk
from queue import Empty, Full, Queue

import cv2
import ttkbootstrap as ttk
from PIL import Image, ImageTk

from crX.utils.battlefield_dedup import load_plan_image


class DedupReview(ttk.Toplevel):
    def __init__(self, parent, plan, keep, on_change):
        super().__init__(parent)
        self.title("全局去重结果")
        self.geometry("1040x760")
        self.minsize(860, 600)
        self.transient(parent.winfo_toplevel())
        self.plan, self.keep, self.on_change = plan, set(keep), on_change
        self._closed = threading.Event()
        self._requests, self._results = Queue(maxsize=1), Queue()
        self._token = 0
        self._arrays = None
        self._photos = []
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self.message = tk.StringVar(
            value="完全重复默认排除；近似组默认保留。相似度忽略 HUD 和手牌变化，保存始终是完整原图。"
        )
        ttk.Label(self, textvariable=self.message, wraplength=1000).grid(row=0, column=0, sticky="ew", padx=12, pady=8)
        actions = ttk.Frame(self)
        actions.grid(row=1, column=0, sticky="ew", padx=12)
        ttk.Button(actions, text="所有近似组仅留代表图", command=self._apply_near).pack(side="left")
        ttk.Button(actions, text="恢复默认选择", command=self._reset).pack(side="left", padx=6)
        self.original_size = tk.BooleanVar(value=False)
        ttk.Checkbutton(actions, text="原尺寸查看（可同步滚动）", variable=self.original_size, command=self._draw).pack(
            side="left"
        )
        self.tree = ttk.Treeview(self, columns=("kind", "count", "kept"), show="headings", height=5)
        for key, title in (("kind", "类型"), ("count", "组内图片"), ("kept", "保留")):
            self.tree.heading(key, text=title)
        self.tree.grid(row=2, column=0, sticky="ew", padx=12, pady=8)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        scroll.grid(row=2, column=1, sticky="ns", pady=8)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._select_group)
        views = ttk.Frame(self)
        views.grid(row=3, column=0, sticky="nsew", padx=12)
        views.columnconfigure((0, 1), weight=1, uniform="images")
        views.rowconfigure(1, weight=1)
        ttk.Label(views, text="左图：本组参考图").grid(row=0, column=0, sticky="w")
        self.members = ttk.Combobox(views, state="readonly", width=1)
        self.members.grid(row=0, column=1, sticky="ew")
        self.members.bind("<<ComboboxSelected>>", lambda _event: self._request_images())
        self.canvases = []
        for column in (0, 1):
            canvas = tk.Canvas(views, background="#202428", highlightthickness=0)
            canvas.grid(row=1, column=column, sticky="nsew", padx=3)
            horizontal = ttk.Scrollbar(views, orient="horizontal", command=lambda *args: self._scroll("x", args))
            horizontal.grid(row=2, column=column, sticky="ew")
            canvas.configure(xscrollcommand=horizontal.set)
            canvas.bind("<Configure>", lambda _event: self._draw())
            canvas.bind(
                "<MouseWheel>", lambda event: self._scroll("y", ("scroll", -1 if event.delta > 0 else 1, "units"))
            )
            self.canvases.append(canvas)
        vertical = ttk.Scrollbar(views, orient="vertical", command=lambda *args: self._scroll("y", args))
        vertical.grid(row=1, column=2, sticky="ns")
        self.canvases[0].configure(yscrollcommand=vertical.set)
        choices = ttk.Frame(self)
        choices.grid(row=4, column=0, sticky="ew", padx=12, pady=8)
        for title, command in (
            ("本组只保留左图", lambda: self._choose("left")),
            ("本组只保留右图", lambda: self._choose("right")),
            ("本组不同图片都保留", lambda: self._choose("all")),
        ):
            ttk.Button(choices, text=title, command=command).pack(side="left", padx=3)
        ttk.Button(choices, text="完成", command=self.destroy).pack(side="right")
        for index, group in enumerate(plan.groups):
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=("近似候选" if group.kind == "near" else "完全重复", len(group.members), 0),
            )
        self._update_choices()
        if plan.groups:
            self.tree.selection_set("0")
        threading.Thread(target=self._read_images, name="dedup-review", daemon=True).start()
        self._poll_id = self.after(80, self._poll)
        self.grab_set()

    def _group(self):
        selection = self.tree.selection()
        return self.plan.groups[int(selection[0])] if selection else None

    def _select_group(self, _event=None):
        group = self._group()
        if group is None:
            return
        self.members.configure(
            values=[
                f"{self.plan.images[index].path.parent.parent.name} / {self.plan.images[index].path.name}"
                for index in group.members
            ]
        )
        candidate = group.default_keep[1] if len(group.default_keep) > 1 else group.members[-1]
        self.members.current(group.members.index(candidate))
        self._request_images()

    def _request_images(self):
        group = self._group()
        if group is None or self.members.current() < 0:
            return
        self._token += 1
        self._arrays = None
        self._draw()
        try:
            self._requests.get_nowait()
        except Empty:
            pass
        try:
            self._requests.put_nowait((self._token, group.representative, group.members[self.members.current()]))
        except Full:
            pass

    def _read_images(self):
        while not self._closed.is_set():
            try:
                token, first, second = self._requests.get(timeout=0.1)
            except Empty:
                continue
            try:
                images = (load_plan_image(self.plan, first), load_plan_image(self.plan, second))
                self._results.put((token, images, None))
            except Exception as error:
                self._results.put((token, None, str(error)))

    def _poll(self):
        try:
            while True:
                token, arrays, error = self._results.get_nowait()
                if token == self._token:
                    self._arrays = arrays
                    if error:
                        self.message.set(error)
                    self._draw()
        except Empty:
            pass
        self._poll_id = self.after(80, self._poll)

    def _scroll(self, axis, args):
        for canvas in self.canvases:
            (canvas.xview if axis == "x" else canvas.yview)(*args)

    def _draw(self):
        self._photos.clear()
        for index, canvas in enumerate(self.canvases):
            canvas.delete("all")
            if self._arrays is None:
                continue
            image = Image.fromarray(cv2.cvtColor(self._arrays[index], cv2.COLOR_BGR2RGB))
            if not self.original_size.get():
                image.thumbnail((max(1, canvas.winfo_width()), max(1, canvas.winfo_height())))
            photo = ImageTk.PhotoImage(image, master=self)
            self._photos.append(photo)
            canvas.create_image(0, 0, image=photo, anchor="nw")
            canvas.configure(scrollregion=(0, 0, image.width, image.height))

    def _choose(self, mode):
        group = self._group()
        if group is None or (mode == "right" and self.members.current() < 0):
            return
        chosen = (
            group.default_keep
            if mode == "all"
            else (group.representative,)
            if mode == "left"
            else (group.members[self.members.current()],)
        )
        self.keep.difference_update(group.members)
        self.keep.update(chosen)
        self._update_choices()

    def _apply_near(self):
        for group in self.plan.groups:
            if group.kind == "near":
                self.keep.difference_update(group.members)
                self.keep.add(group.representative)
        self._update_choices()

    def _reset(self):
        self.keep = set(self.plan.default_keep)
        self._update_choices()

    def _update_choices(self):
        for index, group in enumerate(self.plan.groups):
            self.tree.set(str(index), "kept", len(self.keep.intersection(group.members)))
        self.on_change(set(self.keep))

    def destroy(self):
        self._closed.set()
        if hasattr(self, "_poll_id"):
            self.after_cancel(self._poll_id)
        self.grab_release()
        super().destroy()
