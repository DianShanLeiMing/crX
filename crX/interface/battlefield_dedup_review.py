"""Read-only lists and image previews for automatic representative filtering."""

from __future__ import annotations

import threading
import tkinter as tk
from queue import Empty, Full, Queue

import cv2
import ttkbootstrap as ttk
from PIL import Image, ImageTk

from crX.utils.battlefield_dedup import load_plan_image


class DedupReview(ttk.Toplevel):
    def __init__(self, parent, plan):
        super().__init__(parent)
        self.title("图片筛选结果")
        self.geometry("1040x760")
        self.minsize(860, 600)
        self.transient(parent.winfo_toplevel())
        self.plan = plan
        self.keep = frozenset(plan.default_keep)
        self._closed = threading.Event()
        self._requests, self._results = Queue(maxsize=1), Queue(maxsize=1)
        self._token = 0
        self._image = None
        self._photo = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.message = tk.StringVar(
            value="已按规则自动筛选。点击列表查看图片，导出将直接使用筛选结果；原始图片不删除。"
        )
        ttk.Label(self, textvariable=self.message, wraplength=1000).grid(row=0, column=0, sticky="ew", padx=12, pady=8)

        groups = ttk.Frame(self)
        groups.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        groups.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(groups, columns=("kind", "count", "kept"), show="headings", height=5)
        for key, title in (("kind", "相似分组"), ("count", "图片总数"), ("kept", "将导出")):
            self.tree.heading(key, text=title)
        for key in ("kind", "count", "kept"):
            self.tree.column(key, anchor="center")
        self.tree.grid(row=0, column=0, sticky="ew")
        scroll = ttk.Scrollbar(groups, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._select_group)

        views = ttk.Frame(self)
        views.grid(row=2, column=0, sticky="nsew", padx=12)
        views.columnconfigure(0, weight=2)
        views.columnconfigure(1, weight=3)
        views.rowconfigure(1, weight=1)
        ttk.Label(views, text="本组图片（点击查看）").grid(row=0, column=0, sticky="w")
        self.preview_status = tk.StringVar(value="")
        ttk.Label(views, textvariable=self.preview_status).grid(row=0, column=1, sticky="w", padx=8)
        listing = ttk.Frame(views)
        listing.grid(row=1, column=0, sticky="nsew")
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.members = ttk.Treeview(listing, columns=("name", "result"), show="headings", selectmode="browse")
        self.members.heading("name", text="图片 / 批次")
        self.members.heading("result", text="筛选结果")
        self.members.column("name", width=260, minwidth=160)
        self.members.column("result", width=76, minwidth=76, stretch=False)
        self.members.grid(row=0, column=0, sticky="nsew")
        member_scroll = ttk.Scrollbar(listing, orient="vertical", command=self.members.yview)
        member_scroll.grid(row=0, column=1, sticky="ns")
        self.members.configure(yscrollcommand=member_scroll.set)
        self.members.bind("<<TreeviewSelect>>", self._request_image)
        self.canvas = tk.Canvas(views, background="#202428", highlightthickness=0)
        self.canvas.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        self.canvas.bind("<Configure>", lambda _event: self._draw())
        ttk.Button(self, text="关闭", command=self.destroy).grid(row=3, column=0, sticky="e", padx=12, pady=8)

        for index, group in enumerate(plan.groups):
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    {"near": "战场相同", "scene": "内容相似", "exact": "完全重复"}[group.kind],
                    len(group.members),
                    len(self.keep.intersection(group.members)),
                ),
            )
        if plan.groups:
            self.tree.selection_set("0")
        threading.Thread(target=self._read_images, name="filter-preview", daemon=True).start()
        self._poll_id = self.after(80, self._poll)
        self.grab_set()

    def _group(self):
        selection = self.tree.selection()
        return self.plan.groups[int(selection[0])] if selection else None

    def _select_group(self, _event=None):
        group = self._group()
        if group is None:
            return
        self._token += 1
        self._image = None
        self._draw()
        self.preview_status.set("")
        self.members.delete(*self.members.get_children())
        for index in group.members:
            path = self.plan.images[index].path
            self.members.insert(
                "",
                "end",
                iid=str(index),
                values=(f"{path.name} / {path.parent.parent.name}", "将导出" if index in self.keep else "已筛除"),
            )
        self.members.selection_set(str(group.representative))
        self.members.see(str(group.representative))

    def _request_image(self, _event=None):
        selection = self.members.selection()
        if not selection:
            return
        index = int(selection[0])
        self._token += 1
        self._image = None
        self._draw()
        self.preview_status.set(f"{self.plan.images[index].path.name} · {'将导出' if index in self.keep else '已筛除'}")
        try:
            self._requests.get_nowait()
        except Empty:
            pass
        try:
            self._requests.put_nowait((self._token, index))
        except Full:
            pass

    def _read_images(self):
        while not self._closed.is_set():
            try:
                token, index = self._requests.get(timeout=0.1)
            except Empty:
                continue
            try:
                result = (token, load_plan_image(self.plan, index), None)
            except Exception as error:
                result = (token, None, str(error))
            try:
                self._results.get_nowait()
            except Empty:
                pass
            self._results.put_nowait(result)

    def _poll(self):
        try:
            while True:
                token, image, error = self._results.get_nowait()
                if token == self._token:
                    self._image = image
                    if error:
                        self.message.set(error)
                    self._draw()
        except Empty:
            pass
        self._poll_id = self.after(80, self._poll)

    def _draw(self):
        self.canvas.delete("all")
        self._photo = None
        if self._image is None:
            return
        image = Image.fromarray(cv2.cvtColor(self._image, cv2.COLOR_BGR2RGB))
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        image.thumbnail((width, height))
        self._photo = ImageTk.PhotoImage(image, master=self)
        self.canvas.create_image(width / 2, height / 2, image=self._photo, anchor="center")

    def destroy(self):
        self._closed.set()
        if hasattr(self, "_poll_id"):
            self.after_cancel(self._poll_id)
        self.grab_release()
        super().destroy()
