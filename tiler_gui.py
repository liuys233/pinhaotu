# -*- coding: utf-8 -*-
"""
拼好图（pinhaotu） · Windows 版  v3   ——   主程序

界面结构：
  ┌─────────────────────────────────────────────────────────────┐
  │ 标题                                      相机徽章  画布徽章 │
  ├─────────────────────────────────────────────────────────────┤
  │ 参数条：机型 / 单边上限 / 保护带 / 间隙 / 版式 / 旋转        │
  ├────────────┬────────────────────────────┬───────────────────┤
  │ 照片卡片   │ 画布预览卡片              │ ① ② ③ 步骤按钮    │
  │ (缩略图)   │ (网格 + 阴影 + 编号徽章)   │ 切回设置卡片      │
  │            │ 日志                      │ 省额度卡片        │
  ├────────────┴────────────────────────────┴───────────────────┤
  │ 状态                       进度条 ▓▓▓▓▓░░░░  文案           │
  └─────────────────────────────────────────────────────────────┘

业务逻辑全在 tiler_core.py，这里只管交互与呈现。
"""

from __future__ import annotations

import datetime
import json
import os
import queue
import threading
import time
import traceback
import uuid

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

import tiler_core as core
import tiler_ui as ui

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp")
THUMB = 48          # 列表缩略图边长（要与 Treeview 行高 56 配套）
PREVIEW_MAX = 480   # 画布预览用的中图边长，拖动时再按格子尺寸缩放


# ==========================================================================
# 后台任务
# ==========================================================================
class Worker:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.app = None
        self.root.after(60, self._drain)

    def submit(self, fn, on_done=None, on_error=None):
        def run():
            try:
                res = fn(lambda *a: self.q.put(("p", a)))
                self.q.put(("d", (res, on_done)))
            except Exception as e:
                self.q.put(("e", (e, traceback.format_exc(), on_error)))
        threading.Thread(target=run, daemon=True).start()

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "p":
                    self.root.after(0, lambda p=payload: self._progress(*p))
                elif kind == "d":
                    res, cb = payload
                    if cb:
                        self.root.after(0, lambda r=res, c=cb: c(r))
                else:
                    e, tb, cb = payload
                    if cb:
                        self.root.after(0, lambda x=e, y=tb, c=cb: c(x, y))
        except queue.Empty:
            pass
        self.root.after(60, self._drain)

    def _progress(self, i, n, msg):
        if self.app:
            self.app.set_progress(i, n, msg)


# ==========================================================================
# 主窗口
# ==========================================================================
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.worker = Worker(root)
        self.worker.app = self

        root.title("拼好图 · pinhaotu")
        root.geometry("1500x900")
        root.minsize(1300, 760)
        root.configure(bg=ui.C_BG)

        self.srcs = []
        self.plans = []
        self.current = 0
        self.batch = uuid.uuid4().hex[:10]
        self.thumbs = {}
        self.previews = {}
        self._tile_cache = {}
        self.drag = None
        self._pv = None
        self._hover_k = -1
        self.busy = False
        self.closing = False

        self.v_maxside = tk.IntVar(value=12000)
        self.v_gutter = tk.IntVar(value=core.PIXCAKE_RECOMMENDED_GUTTER)
        self.v_gap = tk.IntVar(value=0)
        self.v_rotate = tk.BooleanVar(value=False)
        self.v_camera = tk.StringVar(value="自动识别")
        self.v_mode = tk.StringVar(value="grid")
        self.v_export = tk.StringVar(value=self._default_export())
        self.v_split_dir = tk.StringVar(value="")
        self.v_fmt = tk.StringVar(value="jpeg")
        self.v_quality = tk.IntVar(value=100)
        self.v_exif = tk.BooleanVar(value=True)
        self.v_ret = tk.StringVar(value="")
        self.v_price = tk.StringVar(value="0.374")

        ui.apply_theme(root)
        self._build()
        self.refresh_ledger()
        self.update_capacity()
        self._refresh_steps()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    @staticmethod
    def _pictures():
        for c in (os.path.join(os.path.expanduser("~"), "Pictures"),
                  os.path.join(os.path.expanduser("~"), "图片")):
            if os.path.isdir(c):
                return c
        return os.path.expanduser("~")

    def _default_export(self):
        return os.path.join(self._pictures(), "拼好图导出")

    def _ledger_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "ledger.json")

    def _on_close(self):
        self.closing = True
        try:
            self.root.destroy()
        except Exception:
            pass

    # ==================================================================
    # 布局
    # ==================================================================
    def _build(self):
        # ---------- 标题条 ----------
        head = tk.Frame(self.root, bg=ui.C_BG)
        head.pack(fill="x", padx=16, pady=(14, 10))

        left = tk.Frame(head, bg=ui.C_BG)
        left.pack(side="left")
        tk.Label(left, text="拼好图", bg=ui.C_BG, fg=ui.C_TEXT,
                 font=ui.F_TITLE).pack(side="left")
        tk.Label(left, text="多张照片 1:1 无损拼合成一张画布 · 精修后按记录切回",
                 bg=ui.C_BG, fg=ui.C_TEXT_FAINT, font=ui.F_SMALL).pack(side="left", padx=(10, 0))

        self.badge_cap = ui.Badge(head, "未排版")
        self.badge_cap.pack(side="right", padx=(8, 0))
        self.badge_cam = ui.Badge(head, "相机：未识别")
        self.badge_cam.pack(side="right")

        # ---------- 参数条 ----------
        bar = tk.Frame(self.root, bg=ui.C_PANEL, highlightthickness=1,
                       highlightbackground=ui.C_BORDER)
        bar.pack(fill="x", padx=16, pady=(0, 10))

        inner = tk.Frame(bar, bg=ui.C_PANEL)
        inner.pack(fill="x", padx=14, pady=10)

        def field(label, factory, w=None):
            f = tk.Frame(inner, bg=ui.C_PANEL)
            f.pack(side="left", padx=(0, 16))
            tk.Label(f, text=label, bg=ui.C_PANEL, fg=ui.C_TEXT_FAINT,
                     font=ui.F_SMALL, anchor="w").pack(fill="x")
            wd = factory(f)
            if w:
                wd.configure(width=w)
            wd.pack(fill="x", pady=(3, 0))
            return wd

        cb = field("相机机型",
                   lambda m: ttk.Combobox(m, textvariable=self.v_camera, state="readonly",
                                          values=["自动识别"] + list(core.CAMERA_PRESETS.keys())),
                   w=24)
        cb.bind("<<ComboboxSelected>>", lambda e: self.update_capacity())

        field("单边上限 px",
              lambda m: ttk.Spinbox(m, from_=2000, to=core.PIXCAKE_HARD_LIMIT,
                                    increment=1000, textvariable=self.v_maxside,
                                    command=self.update_capacity), w=8)
        field("保护带 px",
              lambda m: ttk.Spinbox(m, from_=0, to=64, textvariable=self.v_gutter,
                                    command=self.update_capacity), w=6)
        field("间隙 px",
              lambda m: ttk.Spinbox(m, from_=0, to=200, textvariable=self.v_gap), w=6)
        field("版式",
              lambda m: ttk.Combobox(m, textvariable=self.v_mode, state="readonly",
                                     values=["grid", "row", "col"]), w=8)

        optf = tk.Frame(inner, bg=ui.C_PANEL)
        optf.pack(side="left", padx=(0, 16))
        tk.Label(optf, text="选项", bg=ui.C_PANEL, fg=ui.C_TEXT_FAINT,
                 font=ui.F_SMALL, anchor="w").pack(fill="x")
        ttk.Checkbutton(optf, text="允许旋转 90°（人脸会侧躺）", variable=self.v_rotate,
                        command=self._on_rotate_toggle).pack(anchor="w", pady=(4, 0))

        self.lbl_cap_hint = tk.Label(inner, text="", bg=ui.C_PANEL, fg=ui.C_ACCENT,
                                     font=ui.F_SMALL, anchor="e")
        self.lbl_cap_hint.pack(side="right")

        # ---------- 底部状态条 ----------
        # 注意：必须先于「主体」pack。主体是 expand=True，
        # 若反过来，pack 会把剩余空间全部分给主体，状态条只剩 1px 且不可见。
        foot = tk.Frame(self.root, bg=ui.C_PANEL, highlightthickness=1,
                        highlightbackground=ui.C_BORDER)
        foot.pack(fill="x", side="bottom")
        fi = tk.Frame(foot, bg=ui.C_PANEL)
        fi.pack(fill="x", padx=16, pady=8)

        self.status = tk.StringVar(value="就绪")
        tk.Label(fi, textvariable=self.status, bg=ui.C_PANEL, fg=ui.C_TEXT_DIM,
                 font=ui.F_SMALL, anchor="w").pack(side="left")

        self.lbl_prog = tk.Label(fi, text="", bg=ui.C_PANEL, fg=ui.C_TEXT_FAINT,
                                 font=ui.F_SMALL, width=34, anchor="e")
        self.lbl_prog.pack(side="right")
        self.pb = ttk.Progressbar(fi, mode="determinate", maximum=100, length=220)
        self.pb.pack(side="right", padx=10)

        # ---------- 主体 ----------
        body = tk.Frame(self.root, bg=ui.C_BG)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        # 用 grid 而不是 pack(side)：
        # pack 会按「先装的先占位」切分空间，窗口一变窄右列就被挤出屏幕。
        # grid 的 weight/固定宽度组合在任意窗口尺寸下都稳定。
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=0, minsize=372)   # 左
        body.grid_columnconfigure(1, weight=1)                # 中（吃掉剩余）
        body.grid_columnconfigure(2, weight=0, minsize=340)   # 右

        lcol = tk.Frame(body, bg=ui.C_BG, width=372)
        lcol.grid(row=0, column=0, sticky="nsew")
        lcol.grid_propagate(False)

        mcol = tk.Frame(body, bg=ui.C_BG)
        mcol.grid(row=0, column=1, sticky="nsew", padx=10)

        rcol = tk.Frame(body, bg=ui.C_BG, width=340)
        rcol.grid(row=0, column=2, sticky="nsew")
        rcol.grid_propagate(False)

        self.card_photo = ui.Card(lcol, "照片", "拖入或点击添加")
        self.card_photo.pack(fill="both", expand=True)

        tree_wrap = tk.Frame(self.card_photo.inner, bg=ui.C_PANEL,
                             highlightthickness=1, highlightbackground=ui.C_BORDER)
        tree_wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_wrap, columns=("size", "time"),
                                 show="tree headings",
                                 height=8, selectmode="extended")
        self.tree.heading("#0", text="文件")
        self.tree.heading("size", text="尺寸")
        self.tree.heading("time", text="拍摄")
        self.tree.column("#0", width=150, minwidth=100)
        self.tree.column("size", width=80, minwidth=66, anchor="e")
        self.tree.column("time", width=96, minwidth=88, anchor="center")
        vsb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        tools = tk.Frame(self.card_photo.inner, bg=ui.C_PANEL)
        tools.pack(fill="x", pady=(10, 0))
        ttk.Button(tools, text="＋ 添加照片", command=self.on_add).pack(side="left")
        ttk.Button(tools, text="移除", command=self.on_remove).pack(side="left", padx=6)
        ttk.Button(tools, text="清空", command=self.on_clear).pack(side="left")
        ttk.Button(tools, text="按拍摄时间排序", command=self.on_sort_by_time
                   ).pack(side="left", padx=6)

        self.lbl_stat = tk.Label(self.card_photo.inner, text="", bg=ui.C_PANEL,
                                 fg=ui.C_TEXT_FAINT, font=ui.F_SMALL, anchor="w")
        self.lbl_stat.pack(fill="x", pady=(8, 0))

        # === 中列 ===
        # 副标题位预置一个占位符：Card 只在传入 subtitle 时才创建该标签，
        # 排版后要靠它显示尺寸与「时间跨度」警告，所以这里不能留空
        self.card_canvas = ui.Card(mcol, "画布预览", "—")
        self.card_canvas.pack(fill="both", expand=True)

        nav = tk.Frame(self.card_canvas.inner, bg=ui.C_PANEL)
        nav.pack(fill="x", pady=(0, 8))
        ttk.Button(nav, text="◀", width=3,
                   command=lambda: self.goto(-1)).pack(side="left")
        self.lbl_title = tk.Label(nav, text="尚未排版", bg=ui.C_PANEL,
                                  fg=ui.C_TEXT, font=ui.F_H2)
        self.lbl_title.pack(side="left", padx=12)
        ttk.Button(nav, text="▶", width=3,
                   command=lambda: self.goto(1)).pack(side="left")
        tk.Label(nav, text="画布内可直接拖动照片微调", bg=ui.C_PANEL,
                 fg=ui.C_TEXT_FAINT, font=ui.F_SMALL).pack(side="left", padx=14)
        self.lbl_hintk = tk.Label(nav, text="", bg=ui.C_PANEL, fg=ui.C_ERR,
                                  font=ui.F_SMALL)
        self.lbl_hintk.pack(side="right")

        cv_wrap = tk.Frame(self.card_canvas.inner, bg=ui.C_CANVAS,
                           highlightthickness=1, highlightbackground=ui.C_BORDER)
        cv_wrap.pack(fill="both", expand=True)
        self.cv = tk.Canvas(cv_wrap, bg=ui.C_CANVAS, highlightthickness=0)
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Configure>", lambda e: self.render())
        self.cv.bind("<ButtonPress-1>", self.on_drag_start)
        self.cv.bind("<B1-Motion>", self.on_drag_move)
        self.cv.bind("<ButtonRelease-1>", self.on_drag_end)
        self.cv.bind("<Motion>", self.on_hover)
        self.cv.bind("<Leave>", lambda e: self._set_hover(-1))

        logf = tk.Frame(self.card_canvas.inner, bg=ui.C_PANEL)
        logf.pack(fill="x", pady=(10, 0))
        self.log = tk.Text(logf, height=5, bg=ui.C_PANEL_2, fg=ui.C_TEXT_DIM,
                           font=ui.F_MONO, relief="flat", wrap="word",
                           insertbackground=ui.C_TEXT, padx=10, pady=8,
                           highlightthickness=1,
                           highlightbackground=ui.C_BORDER)
        self.log.pack(fill="x")

        # === 右列 ===
        # 右栏内容比窗口高，放进可滚动容器，否则底部卡片会被切掉看不见。
        self.rcol_scroll = ui.ScrollArea(rcol, bg=ui.C_BG)
        self.rcol_scroll.pack(fill="both", expand=True)
        rbox = self.rcol_scroll.inner

        self.card_step = ui.Card(rbox, "操作流程")
        self.card_step.pack(fill="x")

        self.btn_layout = ui.StepButton(
            self.card_step.inner, 1, "自动排版",
            "在单边上限内搜索最少画布数", self.on_layout, accent=True)
        self.btn_layout.pack(fill="x", pady=(0, 8))

        self.btn_export = ui.StepButton(
            self.card_step.inner, 2, "全部导出画布",
            "生成 TIFF + manifest（切分的唯一依据）", self.on_export)
        self.btn_export.pack(fill="x", pady=(0, 8))

        self.btn_split = ui.StepButton(
            self.card_step.inner, 3, "一键切回原图",
            "按 manifest 精确裁回每张照片", self.on_split)
        self.btn_split.pack(fill="x")

        self.card_split = ui.Card(rbox, "切回设置")
        self.card_split.pack(fill="x", pady=(10, 0))

        tk.Label(self.card_split.inner, text="精修后导出的画布", bg=ui.C_PANEL,
                 fg=ui.C_TEXT_FAINT, font=ui.F_SMALL, anchor="w").pack(fill="x")
        ttk.Entry(self.card_split.inner, textvariable=self.v_ret).pack(
            fill="x", pady=(3, 5))
        ttk.Button(self.card_split.inner, text="选择文件…",
                   command=self.on_pick_ret).pack(anchor="e")

        row = tk.Frame(self.card_split.inner, bg=ui.C_PANEL)
        row.pack(fill="x", pady=(10, 0))
        tk.Label(row, text="格式", bg=ui.C_PANEL, fg=ui.C_TEXT_FAINT,
                 font=ui.F_SMALL).pack(side="left")
        ttk.Combobox(row, textvariable=self.v_fmt, width=8, state="readonly",
                     values=["jpeg", "tiff", "png", "original"]).pack(side="left", padx=5)
        tk.Label(row, text="品质", bg=ui.C_PANEL, fg=ui.C_TEXT_FAINT,
                 font=ui.F_SMALL).pack(side="left", padx=(10, 0))
        ttk.Spinbox(row, from_=60, to=100, textvariable=self.v_quality,
                    width=5).pack(side="left", padx=5)

        ttk.Checkbutton(self.card_split.inner, text="搬回 EXIF（摘缩略图 + 朝向归一）",
                        variable=self.v_exif).pack(anchor="w", pady=(8, 0))

        tk.Label(self.card_split.inner, text="输出目录", bg=ui.C_PANEL,
                 fg=ui.C_TEXT_FAINT, font=ui.F_SMALL, anchor="w").pack(fill="x", pady=(10, 0))
        ttk.Entry(self.card_split.inner, textvariable=self.v_split_dir).pack(
            fill="x", pady=(3, 5))
        ttk.Button(self.card_split.inner, text="选择目录…",
                   command=self.on_pick_splitdir).pack(anchor="e")

        self.card_led = ui.Card(rbox, "省额度")
        self.card_led.pack(fill="x", pady=(10, 4))

        self.lbl_ledger = tk.Label(self.card_led.inner, text="已省 0 次",
                                   bg=ui.C_PANEL, fg=ui.C_ACCENT, font=ui.F_BIG,
                                   anchor="w")
        self.lbl_ledger.pack(fill="x")

        pr = tk.Frame(self.card_led.inner, bg=ui.C_PANEL)
        pr.pack(fill="x", pady=(4, 8))
        tk.Label(pr, text="单张均价 ¥", bg=ui.C_PANEL, fg=ui.C_TEXT_FAINT,
                 font=ui.F_SMALL).pack(side="left")
        pe = ttk.Entry(pr, textvariable=self.v_price, width=8)
        pe.pack(side="left", padx=5)
        pe.bind("<Return>", lambda ev: self.refresh_ledger())
        ttk.Button(pr, text="刷新", command=self.refresh_ledger).pack(side="left")

        self.tree_ledger = ttk.Treeview(self.card_led.inner,
                                        columns=("shots", "money"),
                                        show="headings", height=6,
                                        style="Ledger.Treeview")
        self.tree_ledger.heading("shots", text="省次数")
        self.tree_ledger.heading("money", text="折合 ¥")
        self.tree_ledger.column("shots", width=80, anchor="e")
        self.tree_ledger.column("money", width=90, anchor="e")
        self.tree_ledger.pack(fill="both", expand=True)

        tk.Label(self.card_led.inner, text="导出位置", bg=ui.C_PANEL,
                 fg=ui.C_TEXT_FAINT, font=ui.F_SMALL, anchor="w").pack(
            fill="x", pady=(10, 0))
        ttk.Entry(self.card_led.inner, textvariable=self.v_export).pack(
            fill="x", pady=(3, 5))
        ttk.Button(self.card_led.inner, text="选择目录…",
                   command=self.on_pick_export).pack(anchor="e")

        self._enable_drop()
        self.set_progress(0, 1, "")
        self.log_msg("就绪。把照片拖进窗口，或点「＋ 添加照片」。")

    # ==================================================================
    # 基础
    # ==================================================================
    def log_msg(self, s):
        if self.closing:
            return
        self.log.configure(state="normal")
        self.log.insert("end", s.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_progress(self, i, n, msg):
        if self.closing:
            return
        self.pb["value"] = int(100 * i / n) if n else 0
        self.lbl_prog.configure(text=str(msg)[:40] if msg else "")

    def _refresh_steps(self):
        if self.closing:
            return
        self.btn_layout.set_enabled(bool(self.srcs) and not self.busy)
        self.btn_export.set_enabled(bool(self.plans) and not self.busy)
        self.btn_split.set_enabled(bool(self.v_ret.get()) and not self.busy)

    def _set_busy(self, on):
        self.busy = on
        self._refresh_steps()

    # ------------------------------------------------------------------
    def _enable_drop(self):
        try:
            import ctypes
            from ctypes import wintypes

            u = ctypes.windll.user32
            s = ctypes.windll.shell32
            self.root.update_idletasks()
            hwnd = self.root.winfo_id()
            parent = u.GetParent(hwnd)
            if parent:
                hwnd = parent

            u.DragAcceptFiles(hwnd, True)
            WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, ctypes.c_uint,
                                         wintypes.WPARAM, wintypes.LPARAM)
            u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            u.SetWindowLongPtrW.restype = ctypes.c_void_p
            u.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, ctypes.c_uint,
                                          wintypes.WPARAM, wintypes.LPARAM]
            u.CallWindowProcW.restype = ctypes.c_long
            s.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                         wintypes.LPWSTR, wintypes.UINT]

            self._oldproc = u.SetWindowLongPtrW(hwnd, -4, 0)

            def handler(h, msg, wp, lp):
                if msg == 0x0233:
                    cnt = s.DragQueryFileW(wp, 0xFFFFFFFF, None, 0)
                    files = []
                    for i in range(cnt):
                        n = s.DragQueryFileW(wp, i, None, 0)
                        buf = ctypes.create_unicode_buffer(n + 1)
                        s.DragQueryFileW(wp, i, buf, n + 1)
                        files.append(buf.value)
                    s.DragFinish(wp)
                    if not self.closing:
                        self.root.after(0, lambda f=files: self.add_paths(f))
                    return 0
                return u.CallWindowProcW(self._oldproc, h, msg, wp, lp)

            self._proc = WNDPROC(handler)
            u.SetWindowLongPtrW(hwnd, -4, ctypes.cast(self._proc, ctypes.c_void_p))
        except Exception:
            pass

    # ==================================================================
    # 照片
    # ==================================================================
    def on_add(self):
        files = filedialog.askopenfilenames(
            title="选择照片",
            filetypes=[("图片", " ".join("*" + e for e in IMAGE_EXTS)),
                       ("所有文件", "*.*")])
        self.add_paths(list(files))

    def add_paths(self, files):
        added = 0
        have = {s.path for s in self.srcs}
        for p in files:
            p = os.path.abspath(p)
            if not os.path.isfile(p) or p in have:
                continue
            if os.path.splitext(p)[1].lower() not in IMAGE_EXTS:
                continue
            try:
                w, h, fmt = core.probe_image(p)
                src = core.SourceImage(path=p, name=os.path.basename(p),
                                       natural_w=w, natural_h=h, format=fmt,
                                       bytes=os.path.getsize(p))
                # EXIF：拍摄时间（「同场同光」判断依据）+ 内嵌元数据（原图不在了也能写回）
                core.attach_exif(src)
                src.camera = core.detect_camera_exif(src.model) or core.detect_camera(w, h)
                self.srcs.append(src)
                have.add(p)
                self._add_row(src)
                added += 1
            except Exception as e:
                self.log_msg("跳过 %s：%s" % (os.path.basename(p), e))
        if added:
            self.batch = uuid.uuid4().hex[:10]
            self.plans = []
            self.log_msg("添加 %d 张，共 %d 张" % (added, len(self.srcs)))
            self.update_capacity()
            self._refresh_steps()

    def _add_row(self, src):
        iid = "s%d" % id(src)
        self.tree.insert("", "end", iid=iid, text="  " + src.name,
                         values=("%d×%d" % (src.natural_w, src.natural_h),
                                 self._shot_text(src)))
        self.root.after(20, lambda s=src, i=iid: self._make_thumb(s, i))

    @staticmethod
    def _shot_text(src):
        """拍摄时间显示成 MM-DD HH:MM；没有 EXIF 时间就留空"""
        d = src.shot_dt()
        return d.strftime("%m-%d %H:%M") if d else ""

    def on_sort_by_time(self):
        """按拍摄时间排序照片列表。只影响阅读顺序，不改变排版结果（排版自会按面积搜最优）。"""
        if not self.srcs:
            return
        no_time = [s for s in self.srcs if not s.shot_dt()]
        self.srcs.sort(key=lambda s: s.shot_dt() or datetime.datetime.max)
        self.tree.delete(*self.tree.get_children())
        for s in self.srcs:
            self._add_row(s)
        if no_time:
            self.log_msg("已按拍摄时间排序；%d 张没有拍摄时间，排在末尾" % len(no_time))
        else:
            self.log_msg("已按拍摄时间排序")

    def _make_thumb(self, src, iid):
        if self.closing:
            return
        try:
            im = Image.open(src.path).convert("RGB")
            th = im.copy()
            th.thumbnail((THUMB, THUMB), Image.LANCZOS)
            ph = ImageTk.PhotoImage(th)
            self.thumbs[src.path] = ph
            if self.tree.exists(iid):
                self.tree.item(iid, image=ph)
            # 另存一张中图，供画布预览按格子尺寸缩放使用
            pv = im.copy()
            pv.thumbnail((PREVIEW_MAX, PREVIEW_MAX), Image.LANCZOS)
            self.previews[src.path] = pv
            self._tile_cache.clear()
            if self.plans:
                self.render()
        except Exception:
            pass

    def on_remove(self):
        sel = self.tree.selection()
        if not sel:
            return
        for iid in sel:
            self.tree.delete(iid)
        keep = set(self.tree.get_children())
        self.srcs = [s for s in self.srcs if ("s%d" % id(s)) in keep]
        self.plans = []
        live = {s.path for s in self.srcs}
        for d in (self.thumbs, self.previews):
            for p in list(d):
                if p not in live:
                    d.pop(p, None)
        self._tile_cache.clear()
        self.update_capacity()
        self.render()
        self._refresh_steps()

    def on_clear(self):
        self.srcs.clear()
        self.plans.clear()
        self.thumbs.clear()
        self.previews.clear()
        self._tile_cache.clear()
        self.tree.delete(*self.tree.get_children())
        self.cv.delete("all")
        self.lbl_title.configure(text="尚未排版")
        self.lbl_stat.configure(text="")
        self.badge_cap.set("未排版")
        self._refresh_steps()
        self.log_msg("已清空")

    # ==================================================================
    # 相机 / 容量
    # ==================================================================
    def update_capacity(self):
        if self.closing:
            return
        name = self.v_camera.get()
        w = h = 0
        label = ""
        if name == "自动识别":
            cs = [s.camera for s in self.srcs if s.camera]
            if cs:
                name = cs[0]
            elif self.srcs:
                w, h = self.srcs[0].natural_w, self.srcs[0].natural_h
                label = "%d×%d" % (w, h)
        if name and name in core.CAMERA_PRESETS:
            w, h = core.CAMERA_PRESETS[name]["w"], core.CAMERA_PRESETS[name]["h"]
            label = name
        if not w:
            self.badge_cam.set("相机：未识别")
            self.lbl_cap_hint.configure(text="")
            return
        cap = core.capacity_report(w, h, self.v_maxside.get(), self.v_gutter.get())
        ori = {"P": "竖", "L": "横", "S": "方"}.get(core.orientation_of(w, h), "")
        self.badge_cam.set("相机：%s%s" % (label, "（%s）" % ori if ori else ""))
        self.lbl_cap_hint.configure(
            text="单边 %dpx 单画布：竖 %d / 横 %d / 横竖混排 %d 张"
                 % (self.v_maxside.get(), cap["portrait"]["total"],
                    cap["landscape"]["total"], cap["mixed"]))

    def _on_rotate_toggle(self):
        self.update_capacity()
        if self.v_rotate.get():
            self.log_msg(
                "已开启「允许旋转 90°」：排版器会把个别照片转 90° 塞进窄列来提升密度，"
                "被转的那几张人脸会侧躺，像素蛋糕的人脸识别可能失效 —— 修人像请慎用。")

    # ==================================================================
    # 排版
    # ==================================================================
    def on_layout(self):
        if not self.srcs:
            messagebox.showwarning("提示", "先添加照片")
            return
        mx = self.v_maxside.get()
        if mx > core.PIXCAKE_HARD_LIMIT:
            if messagebox.askyesno(
                    "超出导入硬上限",
                    "%dpx 超过像素蛋糕的导入硬上限 %dpx。\n\n"
                    "导入时会直接弹「图片尺寸过大」被拒 —— 连静默降采样都不会发生，"
                    "这一批等于白拼。\n\n是否改用推荐值 %dpx 继续？"
                    % (mx, core.PIXCAKE_HARD_LIMIT, core.PIXCAKE_SAFE_LIMIT)):
                self.v_maxside.set(core.PIXCAKE_SAFE_LIMIT)
                mx = core.PIXCAKE_SAFE_LIMIT
            else:
                return

        rot = self.v_rotate.get()
        if rot:
            self.log_msg("注意：已开旋转，个别照片会被转 90°（人脸侧躺）以提升密度。")
        # 朝向交给排版器决定：默认全部保持真实朝向（人脸朝上），这里不预置任何旋转
        if not rot:
            n_p = sum(1 for s in self.srcs if s.orientation == "P")
            n_l = sum(1 for s in self.srcs if s.orientation == "L")
            self.log_msg("朝向：竖图 %d 张（竖着排）/ 横图 %d 张（横着排），"
                         "不旋转，人脸保持朝上。" % (n_p, n_l))

        gut, gap, mode = self.v_gutter.get(), self.v_gap.get(), self.v_mode.get()
        if gut <= 0 and not messagebox.askyesno(
                "保护带为 0",
                "保护带 0 意味着接缝两侧没有自身镜像，像素蛋糕里的液化会直接拉扯到相邻照片，"
                "跨缝就无法保证还原。\n\n确定要用 0px 继续排版？"):
            return
        self.status.set("正在排版…")
        self._set_busy(True)
        self.set_progress(0, 1, "搜索最优排版")

        def job(prog):
            if mode in ("row", "col"):
                return core.pack_strip(self.srcs, mx, axis=mode, gap=gap,
                                       gutter=gut, progress=prog)
            return core.pack_canvases(self.srcs, mx, gap=gap, gutter=gut,
                                      allow_rotate=rot, progress=prog)

        def done(plans):
            self._set_busy(False)
            self.plans = plans
            self.current = 0
            saved = max(0, len(self.srcs) - len(plans))
            self.status.set("排版完成：%d 张画布" % len(plans))
            self.log_msg("排版完成：%d 张 → %d 张画布（省 %d 次）"
                         % (len(self.srcs), len(plans), saved))
            for p in plans:
                self.log_msg("  画布 %d：%d×%d，%d 张，填充 %.1f%%"
                             % (p.index + 1, p.width, p.height,
                                len(p.placements), p.fill() * 100))
            self.lbl_stat.configure(
                text="%d 张 → %d 张画布（省 %d 次）" % (len(self.srcs), len(plans), saved))
            self.badge_cap.set("%d 画布 · 省 %d 次" % (len(plans), saved))
            self.set_progress(1, 1, "完成")
            self.render()
            self._refresh_steps()

        def err(e, tb):
            self._set_busy(False)
            self.status.set("排版失败")
            self.log_msg("排版失败：%s" % e)
            messagebox.showerror("排版失败", str(e))

        self.worker.submit(job, done, err)

    def goto(self, d):
        if not self.plans:
            return
        self.current = (self.current + d) % len(self.plans)
        self.render()

    # ==================================================================
    # 渲染
    # ==================================================================
    def render(self):
        if self.closing:
            return
        self.cv.delete("all")
        cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
        if cw < 60 or ch < 60:
            return
        if not self.plans:
            self.cv.create_text(cw / 2, ch / 2 - 10, text="还没有画布",
                                fill=ui.C_TEXT_FAINT, font=ui.F_H2)
            self.cv.create_text(cw / 2, ch / 2 + 14,
                                text="点右侧「① 自动排版」开始",
                                fill=ui.C_TEXT_FAINT, font=ui.F_SMALL)
            return

        plan = self.plans[self.current]
        self.lbl_title.configure(
            text="画布 %d / %d" % (self.current + 1, len(self.plans)))
        sub = ("%d × %d · %d 张 · 填充 %.0f%%"
               % (plan.width, plan.height, len(plan.placements), plan.fill() * 100))
        # 把「同场同光」的判断依据摆出来：画布内拍摄时间跨度太大就标出来
        span = core.scene_time_span(plan, self.srcs)
        if span is not None and span > core.SAME_SCENE_MAX_MINUTES:
            sub += "  ⚠ 时间跨度 %s" % core.format_span(span)
            self.card_canvas.set_subtitle(sub, warn=True)
        else:
            self.card_canvas.set_subtitle(sub)

        pad = 20
        s = min((cw - 2 * pad) / float(plan.width), (ch - 2 * pad) / float(plan.height))
        ox = (cw - plan.width * s) / 2
        oy = (ch - plan.height * s) / 2
        self._pv = (plan, s, ox, oy)

        # 背景细网格
        step = 32
        gx = ox
        while gx < ox + plan.width * s:
            self.cv.create_line(gx, oy, gx, oy + plan.height * s, fill="#eff2f6")
            gx += step
        gy = oy
        while gy < oy + plan.height * s:
            self.cv.create_line(ox, gy, ox + plan.width * s, gy, fill="#eff2f6")
            gy += step

        # 阴影 + 画布面
        x2, y2 = ox + plan.width * s, oy + plan.height * s
        for i in (4, 3, 2, 1):
            self.cv.create_rectangle(ox + i, oy + i, x2 + i, y2 + i,
                                     fill="#dfe4eb", outline="")
        self.cv.create_rectangle(ox, oy, x2, y2, fill="#ffffff", outline="#c9d2dd")

        g = self.v_gutter.get() * s
        for k, pl in enumerate(plan.placements):
            x0, y0 = ox + pl.x * s, oy + pl.y * s
            x1, y1 = x0 + pl.w * s, y0 + pl.h * s
            hovered = (k == self._hover_k)

            if g > 0.4:
                self.cv.create_rectangle(
                    x0 - g, y0 - g, x1 + g, y1 + g,
                    outline=(ui.C_ACCENT if hovered else "#c99a2e"),
                    dash=(3, 2), width=1)

            src = self.srcs[pl.src_index]
            # 缩略图按格子尺寸缩放后贴上去，而不是丢一个固定小图在中间
            ph = self._tile_image(src.path, int(x1 - x0), int(y1 - y0))
            if ph is not None:
                self.cv.create_image((x0 + x1) / 2, (y0 + y1) / 2, image=ph)
            else:
                self.cv.create_rectangle(x0, y0, x1, y1,
                                         fill=("#c8ddf8" if hovered else "#dfeaf9"),
                                         outline="")
            self.cv.create_rectangle(x0, y0, x1, y1,
                                     outline=(ui.C_ACCENT if hovered else "#c9d2dd"),
                                     width=(2 if hovered else 1))

            if (x1 - x0) > 28 and (y1 - y0) > 22:
                self.cv.create_rectangle(x0 + 5, y0 + 5, x0 + 27, y0 + 22,
                                         fill="#1d2530", outline="#1d2530")
                self.cv.create_text(x0 + 16, y0 + 13, text=str(k + 1), fill="#ffffff",
                                    font=("Consolas", 9, "bold"))

            if (x1 - x0) > 120 and (y1 - y0) > 42:
                tag = "%d×%d" % (pl.w, pl.h)
                tw = 6.7 * len(tag) + 10
                self.cv.create_rectangle(x0 + 5, y1 - 21, x0 + 5 + tw, y1 - 5,
                                         fill="#1d2530", outline="")
                self.cv.create_text(x0 + 10, y1 - 13, text=tag, anchor="w",
                                    fill="#ffffff", font=("Consolas", 8))

    def _tile_image(self, path, dw, dh):
        """把中图按目标格子尺寸缩放成 PhotoImage，按 (路径, 尺寸) 缓存。"""
        if dw < 12 or dh < 12:
            return None
        pv = self.previews.get(path)
        if pv is None:
            return None
        key = (path, dw, dh)
        cached = self._tile_cache.get(key)
        if cached is None:
            if len(self._tile_cache) > 240:
                self._tile_cache.clear()
            cached = ImageTk.PhotoImage(pv.resize((dw, dh), Image.LANCZOS))
            self._tile_cache[key] = cached
        return cached

    def on_hover(self, e):
        if not self._pv:
            return
        plan, s, ox, oy = self._pv
        mx, my = (e.x - ox) / s, (e.y - oy) / s
        k = -1
        for i, pl in enumerate(plan.placements):
            if pl.x <= mx <= pl.x + pl.w and pl.y <= my <= pl.y + pl.h:
                k = i
                break
        self._set_hover(k)

    def _set_hover(self, k):
        if k == self._hover_k:
            return
        self._hover_k = k
        self.cv.configure(cursor="fleur" if k >= 0 else "")
        self.render()

    def _hit(self, ex, ey):
        if not self._pv:
            return None
        plan, s, ox, oy = self._pv
        mx, my = (ex - ox) / s, (ey - oy) / s
        for k, pl in enumerate(plan.placements):
            if pl.x <= mx <= pl.x + pl.w and pl.y <= my <= pl.y + pl.h:
                return k, mx - pl.x, my - pl.y
        return None

    def on_drag_start(self, e):
        h = self._hit(e.x, e.y)
        if h:
            self.drag = h

    def on_drag_move(self, e):
        if not self.drag or not self._pv:
            return
        plan, s, ox, oy = self._pv
        k, dx, dy = self.drag
        pl = plan.placements[k]
        # 贴边也要留住保护带，否则镜像条会被裁掉、跨缝保护失效
        g = self.v_gutter.get()
        mx = max(g, plan.width - pl.w - g)
        my = max(g, plan.height - pl.h - g)
        pl.x = int(max(g, min(mx, (e.x - ox) / s - dx)))
        pl.y = int(max(g, min(my, (e.y - oy) / s - dy)))
        self._overlap_check()

    def on_drag_end(self, e):
        self.drag = None
        self._overlap_check()

    def _overlap_check(self):
        if not self._pv:
            return
        plan = self._pv[0]
        g = self.v_gutter.get()
        bad = 0
        n = len(plan.placements)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = plan.placements[i], plan.placements[j]
                if not (a.x + a.w + g <= b.x - g or b.x + b.w + g <= a.x - g or
                        a.y + a.h + g <= b.y - g or b.y + b.h + g <= a.y - g):
                    bad += 1
        if bad:
            self.lbl_hintk.configure(text="⚠ %d 处重叠（含保护带）" % bad)
        else:
            self.lbl_hintk.configure(text="")
        plan.width = max(p.x + p.w + g for p in plan.placements)
        plan.height = max(p.y + p.h + g for p in plan.placements)
        self.render()

    # ==================================================================
    # 导出
    # ==================================================================
    def on_pick_export(self):
        d = filedialog.askdirectory()
        if d:
            self.v_export.set(d)

    def on_export(self):
        if not self.plans:
            messagebox.showwarning("提示", "先点「① 自动排版」")
            return
        parent = self.v_export.get().strip()
        if not parent:
            messagebox.showwarning("提示", "先设置导出位置")
            return
        os.makedirs(parent, exist_ok=True)
        plans, srcs = self.plans, self.srcs
        batch = self.batch
        gut, gap = self.v_gutter.get(), self.v_gap.get()
        mx = self.v_maxside.get()
        # manifest 记的是这批**实际**有没有照片被转过，而不是开关状态
        rot = any(p.rotation == 90 for pl in plans for p in pl.placements)
        cam = "" if self.v_camera.get() == "自动识别" else self.v_camera.get()
        total = len(plans)
        self.status.set("正在导出…")
        self._set_busy(True)
        self.set_progress(0, 1, "准备")
        t0 = time.time()

        def job(prog):
            for i, s in enumerate(srcs):
                if not s.sha256:
                    s.sha256 = core.sha256_file(s.path)
                prog(i + 1, len(srcs) + total, "校验 %d/%d" % (i + 1, len(srcs)))
            # 导出前做纯数学校验：拖拽后若有越界/保护带相交，宁可拦住也不要写出坏画布
            for plan in plans:
                errs = core.verify_plan(plan, srcs, mx, gut)
                if errs:
                    raise ValueError("画布 %d 校验未通过：%s（请重新拖拽或重新排版）"
                                     % (plan.index + 1, "；".join(errs[:3])))
            outs = []
            for k, plan in enumerate(plans):
                out = os.path.join(parent, "TILE_%s_c%02d.tif" % (batch, plan.index + 1))
                prog(len(srcs) + k, len(srcs) + total,
                     "合成画布 %d/%d" % (k + 1, total))
                core.compose_canvas(
                    plan, srcs, out, gutter=gut,
                    progress=lambda i, n_, nm, kk=k: prog(
                        len(srcs) + kk, len(srcs) + total,
                        "画布 %d：%d/%d" % (kk + 1, i, n_)))
                man = core.build_manifest(plan, srcs, out, gut, gap, batch, rot, mx, cam)
                outs.append((out, core.write_manifest(man, out), len(plan.placements)))
            return outs

        def done(outs):
            self._set_busy(False)
            dt = time.time() - t0
            self.status.set("导出完成（%.1fs）" % dt)
            for out, mp, n in outs:
                self.log_msg("✔ %s  %s  %d 张"
                             % (os.path.basename(out),
                                core.human_size(os.path.getsize(out)), n))
            overs = [o for o, _, _ in outs
                     if os.path.getsize(o) > core.PIXCAKE_MAX_FILE_BYTES]
            if overs:
                self.log_msg("⚠ 有 %d 张画布超过 1GB，像素蛋糕会拒绝导入：%s"
                             % (len(overs), "、".join(os.path.basename(o) for o in overs)))
            self.log_msg("目录：%s" % parent)
            saved = max(0, len(srcs) - len(plans))
            core.ledger_add(self._ledger_path(), {
                "batch": batch, "time": core.now_iso(),
                "images": len(srcs), "canvases": len(plans),
                "savedShots": saved, "removed": False,
                "camera": cam or "?",
            })
            self.refresh_ledger()
            if outs:
                self.v_ret.set(outs[0][0])
                self.v_split_dir.set(os.path.join(parent, "切回原图"))
            self.set_progress(1, 1, "完成")
            self._refresh_steps()
            messagebox.showinfo(
                "导出完成",
                "共 %d 张画布，省 %d 次额度。\n\n"
                "下一步：\n"
                "1) .tif 导入像素蛋糕\n"
                "2) 只做调色，禁用液化 / AI 天空 / 裁剪\n"
                "3) 导出选 TIFF，别改文件名\n"
                "4) 回来点「③ 一键切回原图」" % (len(outs), saved))

        def err(e, tb):
            self._set_busy(False)
            self.status.set("导出失败")
            self.log_msg("导出失败：%s" % e)
            self.log_msg(tb[-1000:])
            messagebox.showerror("导出失败", str(e))

        self.worker.submit(job, done, err)

    # ==================================================================
    # 切回
    # ==================================================================
    def on_pick_ret(self):
        p = filedialog.askopenfilename(
            title="选择精修后导出的画布",
            filetypes=[("图片", "*.tif *.tiff *.jpg *.jpeg *.png"), ("所有文件", "*.*")])
        if p:
            self.v_ret.set(p)
            if not self.v_split_dir.get():
                self.v_split_dir.set(os.path.join(os.path.dirname(p), "切回原图"))
            self._refresh_steps()

    def on_pick_splitdir(self):
        d = filedialog.askdirectory()
        if d:
            self.v_split_dir.set(d)

    def _find_manifest(self, canvas):
        cand = core.manifest_path_for(canvas)
        if os.path.exists(cand):
            return cand
        d = os.path.dirname(os.path.abspath(canvas))
        base = os.path.basename(canvas)
        try:
            for f in os.listdir(d):
                if not f.endswith(".manifest.json"):
                    continue
                try:
                    with open(os.path.join(d, f), "r", encoding="utf-8") as fh:
                        m = json.load(fh)
                    if m.get("canvas", {}).get("file") == base:
                        return os.path.join(d, f)
                except Exception:
                    continue
        except OSError:
            pass
        return None

    def on_split(self):
        ret = self.v_ret.get().strip()
        if not ret or not os.path.exists(ret):
            messagebox.showwarning("提示", "先选择精修后的画布文件")
            return
        mp = self._find_manifest(ret)
        if not mp:
            messagebox.showerror(
                "找不到记录",
                "没有找到 %s 的 .manifest.json。\n\n"
                "不确定对应关系时不要猜着切 —— 会把别的画布切坏。" % os.path.basename(ret))
            return
        with open(mp, "r", encoding="utf-8") as f:
            man = json.load(f)
        outdir = self.v_split_dir.get().strip() or os.path.join(
            os.path.dirname(ret), "切回原图")
        n = len(man.get("items", []))
        cw, chh = man["canvas"]["width"], man["canvas"]["height"]
        note = ""
        try:
            aw, ah = Image.open(ret).size
            if (aw, ah) != (cw, chh):
                note = "\n\n⚠ 尺寸 %d×%d ≠ 画布 %d×%d（将按比例映射）" % (aw, ah, cw, chh)
        except Exception:
            pass
        if not messagebox.askyesno("确认切回",
                                   "画布 %s\n切出 %d 张 → %s%s\n\n继续？"
                                   % (os.path.basename(ret), n, outdir, note)):
            return

        fmt, q, ke = self.v_fmt.get(), self.v_quality.get(), self.v_exif.get()
        self.status.set("正在切分…")
        self._set_busy(True)
        self.set_progress(0, 1, "读取画布")

        def job(prog):
            return core.split_canvas(ret, man, outdir, fmt=fmt, quality=q,
                                     keep_exif=ke, progress=prog)

        def done(rep):
            self._set_busy(False)
            ok = [o for o in rep["outputs"] if o.get("file")]
            bad = [o for o in rep["outputs"] if o.get("error")]
            self.status.set("切回完成：%d 张" % len(ok))
            for w in rep.get("warnings", []):
                self.log_msg("⚠ %s" % w)
            for o in ok:
                self.log_msg("✔ %s  %s%s%s"
                             % (o["name"], o["size"],
                                "  [已转正]" if o.get("rotation") else "",
                                "  [EXIF]" if o.get("exifCopied") else ""))
            for o in bad:
                self.log_msg("✘ %s：%s" % (o["name"], o.get("error")))
            self.set_progress(1, 1, "完成")
            messagebox.showinfo("完成", "已切出 %d 张\n%s" % (len(ok), outdir))
            try:
                os.startfile(outdir)
            except Exception:
                pass

        def err(e, tb):
            self._set_busy(False)
            self.status.set("切分失败")
            self.log_msg("切分失败：%s" % e)
            messagebox.showerror("切分失败", str(e))

        self.worker.submit(job, done, err)

    # ==================================================================
    # 账本
    # ==================================================================
    def refresh_ledger(self):
        if self.closing:
            return
        try:
            price = float(self.v_price.get())
        except ValueError:
            price = core.PRICE_PER_SHOT_DEFAULT
        s = core.ledger_summary(self._ledger_path(), price)
        self.lbl_ledger.configure(
            text="已省 %d 次 · ¥%.2f" % (s["savedShots"], s["savedMoney"]))
        self.tree_ledger.delete(*self.tree_ledger.get_children())
        for b in list(reversed(s["batches"]))[:60]:
            shots = int(b.get("savedShots", 0) or 0)
            self.tree_ledger.insert("", "end",
                                    values=(shots, "%.2f" % (shots * price)))


def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        sys.exit(core.self_test())

    # 高 DPI：让 Windows 缩放（125% / 150%）下画面不糊。
    # 必须在创建 Tk 之前声明，否则 Tk 会被系统位图拉伸。
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

    root = tk.Tk()

    # 按真实分辨率自适应窗口，避免超出屏幕
    try:
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
    except Exception:
        sw, sh = 1920, 1080
    # 左右两列是固定宽度（372 + 340 + 边距），中列至少要 600 才看得清画布
    w = min(1500, max(1360, int(sw * 0.86)))
    w = min(w, sw - 40)
    h = min(900, max(740, int(sh * 0.84)))
    h = min(h, sh - 80)
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 3)
    root.geometry("%dx%d+%d+%d" % (w, h, x, y))

    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
