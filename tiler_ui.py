# -*- coding: utf-8 -*-
"""
tiler_ui.py — 拼好图（pinhaotu） v3 的界面组件层

只放"外观"，不放业务。App 只关心：我要一个卡片、一个步骤按钮、一个徽章。
所有尺寸常量都集中在 TOK，改一个数就能整体缩放。
"""

import tkinter as tk
from tkinter import ttk

# ==========================================================================
# 设计令牌
# ==========================================================================
C_BG = "#eef1f6"          # 窗口底色（浅灰）
C_PANEL = "#ffffff"        # 卡片面
C_PANEL_2 = "#f3f6fa"      # 卡片内的次级面（输入框 / 次级按钮）
C_PANEL_3 = "#e5eaf1"      # 再深一档（hover / 凹陷面）
C_BORDER = "#dbe0e8"       # 描边
C_TEXT = "#1d2530"
C_TEXT_DIM = "#57616f"
C_TEXT_FAINT = "#8b95a3"
C_ACCENT = "#2f7bf6"
C_ACCENT_DK = "#1f66d8"
C_OK = "#1f9d55"
C_WARN = "#b8781a"
C_ERR = "#d9453f"
C_CANVAS = "#e9edf3"       # 画布预览底

C_SEL = "#d9e7fe"          # 列表选中底色（浅蓝）
C_GHOST = "#f7f9fc"        # 禁用态底色

F_TITLE = ("Microsoft YaHei UI", 14, "bold")
F_H2 = ("Microsoft YaHei UI", 10, "bold")
F_BODY = ("Microsoft YaHei UI", 9)
F_SMALL = ("Microsoft YaHei UI", 8)
F_BIG = ("Microsoft YaHei UI", 16, "bold")
F_MONO = ("Consolas", 9)

PAD = 12
GAP = 8


# ==========================================================================
# 圆角矩形辅助（用 canvas 画，避免 tk 没有圆角控件）
# ==========================================================================
def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


class Card(tk.Frame):
    """卡片：标题栏 + 内容区"""

    def __init__(self, master, title="", subtitle="", pad=(14, 12)):
        super().__init__(master, bg=C_PANEL, highlightthickness=1,
                         highlightbackground=C_BORDER,
                         highlightcolor=C_BORDER)
        if title:
            head = tk.Frame(self, bg=C_PANEL)
            head.pack(fill="x", padx=pad[0], pady=(pad[1], 0))
            tk.Label(head, text=title, bg=C_PANEL, fg=C_TEXT,
                     font=F_H2).pack(side="left")
            self._sub = None
            if subtitle:
                self._sub = tk.Label(head, text=subtitle, bg=C_PANEL, fg=C_TEXT_FAINT,
                                     font=F_SMALL)
                self._sub.pack(side="left", padx=(8, 0))
        self.inner = tk.Frame(self, bg=C_PANEL)
        self.inner.pack(fill="both", expand=True,
                        padx=pad[0], pady=(6, pad[1]))

    def set_subtitle(self, text, warn=False):
        if self._sub is not None:
            self._sub.configure(text=text, fg=C_WARN if warn else C_TEXT_FAINT)


class Badge(tk.Label):
    """状态徽章"""

    def __init__(self, master, text="", bg=None, fg=None):
        super().__init__(master, text=text, bg=bg or C_PANEL,
                         fg=fg or C_TEXT_DIM, font=F_SMALL,
                         padx=10, pady=3, bd=0,
                         highlightthickness=1,
                         highlightbackground=C_BORDER,
                         highlightcolor=C_BORDER)

    def set(self, text, bg=None, fg=None):
        self.configure(text=text)
        if bg:
            self.configure(bg=bg)
        if fg:
            self.configure(fg=fg)


class StepButton(tk.Frame):
    """
    步骤按钮：左侧方形序号 + 标题/说明。
    accent=True 时是"主行动"（蓝底）。
    enabled=False 时变灰且不可点。
    """

    def __init__(self, master, step, title, desc, command, accent=False):
        self._accent = accent
        self._cmd = command
        self._enabled = True
        self._base = C_ACCENT if accent else C_PANEL_2
        self._hover = C_ACCENT_DK if accent else C_PANEL_3
        self._disabled = C_GHOST

        super().__init__(master, bg=self._base, highlightthickness=1,
                         highlightbackground=(C_ACCENT if accent else C_BORDER),
                         highlightcolor=(C_ACCENT if accent else C_BORDER),
                         cursor="hand2")

        self._num_box = tk.Frame(self, bg="white" if accent else C_PANEL_3, width=28, height=28)
        self._num_box.pack(side="left", padx=(10, 10), pady=11)
        self._num_box.pack_propagate(False)
        self._num = tk.Label(self._num_box, text=str(step),
                             bg="white" if accent else C_ACCENT,
                             fg=C_ACCENT if accent else "white",
                             font=("Microsoft YaHei UI", 10, "bold"))
        self._num.pack(expand=True)

        self._body = tk.Frame(self, bg=self._base)
        self._body.pack(side="left", fill="both", expand=True, pady=9)
        self._title = tk.Label(self._body, text=title, bg=self._base,
                               fg="white" if accent else C_TEXT,
                               font=F_H2, anchor="w")
        self._title.pack(fill="x")
        self._desc = tk.Label(self._body, text=desc, bg=self._base,
                              fg="#dbe9ff" if accent else C_TEXT_FAINT,
                              font=F_SMALL, anchor="w")
        self._desc.pack(fill="x")

        for w in (self, self._body, self._title, self._desc, self._num_box, self._num):
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)

    def _paint(self, bg):
        self.configure(bg=bg)
        self._body.configure(bg=bg)
        self._title.configure(bg=bg)
        self._desc.configure(bg=bg)
        self._num_box.configure(bg=("white" if self._accent else C_PANEL_3) if bg != self._disabled else C_PANEL_3)
        if bg == self._disabled:
            edge = "#eef1f5"
        elif bg == self._hover and not self._accent:
            edge = C_ACCENT
        else:
            edge = C_ACCENT if self._accent else C_BORDER
        self.configure(highlightbackground=edge, highlightcolor=edge)

    def _enter(self, _):
        if self._enabled:
            self._paint(self._hover)

    def _leave(self, _):
        if self._enabled:
            self._paint(self._base)

    def _click(self, _):
        if self._enabled and self._cmd:
            self._cmd()

    def set_enabled(self, on):
        self._enabled = bool(on)
        self.configure(cursor="hand2" if on else "arrow")
        if on:
            self._paint(self._base)
            self._title.configure(fg="white" if self._accent else C_TEXT)
            self._desc.configure(fg="#dbe9ff" if self._accent else C_TEXT_FAINT)
            self._num.configure(bg="white" if self._accent else C_ACCENT,
                                fg=C_ACCENT if self._accent else "white")
        else:
            self._paint(self._disabled)
            self._title.configure(fg=C_TEXT_FAINT)
            self._desc.configure(fg="#b7c0cc")
            self._num.configure(bg=C_PANEL_3, fg=C_TEXT_FAINT)


def apply_theme(root):
    """统一 ttk 控件外观（亮色）"""
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except Exception:
        pass

    st.configure(".", background=C_PANEL, foreground=C_TEXT,
                 font=F_BODY, borderwidth=0, focuscolor=C_PANEL)
    st.configure("TFrame", background=C_PANEL)
    st.configure("TLabel", background=C_PANEL, foreground=C_TEXT)
    st.configure("Dim.TLabel", background=C_PANEL, foreground=C_TEXT_DIM)
    st.configure("Faint.TLabel", background=C_PANEL, foreground=C_TEXT_FAINT)
    st.configure("Head.TLabel", background=C_PANEL, foreground=C_TEXT, font=F_H2)
    st.configure("Big.TLabel", background=C_PANEL, foreground=C_ACCENT, font=F_BIG)

    # 次级按钮：浅灰面 + 1px 描边，hover 稍深
    st.configure("TButton", background=C_PANEL_2, foreground=C_TEXT,
                 padding=(10, 5), font=F_BODY, relief="flat", borderwidth=1,
                 bordercolor=C_BORDER, lightcolor=C_PANEL_2, darkcolor=C_PANEL_2)
    st.map("TButton",
           background=[("pressed", C_PANEL_3), ("active", C_PANEL_3),
                       ("disabled", C_GHOST)],
           bordercolor=[("active", C_TEXT_FAINT), ("disabled", "#eef1f5")],
           foreground=[("disabled", C_TEXT_FAINT)])

    st.configure("Accent.TButton", background=C_ACCENT, foreground="white",
                 bordercolor=C_ACCENT, lightcolor=C_ACCENT, darkcolor=C_ACCENT)
    st.map("Accent.TButton",
           background=[("active", C_ACCENT_DK), ("pressed", C_ACCENT_DK)],
           bordercolor=[("active", C_ACCENT_DK), ("pressed", C_ACCENT_DK)])

    st.configure("TEntry", fieldbackground=C_PANEL_2, background=C_PANEL_2,
                 foreground=C_TEXT, insertcolor=C_TEXT, padding=5, borderwidth=1,
                 bordercolor=C_BORDER, lightcolor=C_PANEL_2, darkcolor=C_PANEL_2)
    st.map("TEntry", bordercolor=[("focus", C_ACCENT)])
    st.configure("TSpinbox", fieldbackground=C_PANEL_2, background=C_PANEL_2,
                 foreground=C_TEXT, insertcolor=C_TEXT, arrowsize=13, padding=4,
                 borderwidth=1, bordercolor=C_BORDER, arrowcolor=C_TEXT_DIM,
                 lightcolor=C_PANEL_2, darkcolor=C_PANEL_2)
    st.map("TSpinbox",
           bordercolor=[("focus", C_ACCENT)],
           arrowcolor=[("active", C_ACCENT)])
    st.configure("TCombobox", fieldbackground=C_PANEL_2, background=C_PANEL_2,
                 foreground=C_TEXT, arrowcolor=C_TEXT_DIM, padding=4, borderwidth=1,
                 bordercolor=C_BORDER, lightcolor=C_PANEL_2, darkcolor=C_PANEL_2)
    st.map("TCombobox",
           fieldbackground=[("readonly", C_PANEL_2)],
           background=[("readonly", C_PANEL_2), ("active", C_PANEL_3)],
           foreground=[("readonly", C_TEXT)],
           bordercolor=[("focus", C_ACCENT), ("readonly", C_BORDER)],
           arrowcolor=[("active", C_ACCENT)],
           selectbackground=[("readonly", C_PANEL_2)],
           selectforeground=[("readonly", C_TEXT)])
    root.option_add("*TCombobox*Listbox.background", C_PANEL)
    root.option_add("*TCombobox*Listbox.foreground", C_TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", C_ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "white")

    st.configure("TCheckbutton", background=C_PANEL, foreground=C_TEXT,
                 indicatorcolor=C_PANEL, bordercolor=C_TEXT_FAINT,
                 focuscolor=C_PANEL)
    st.map("TCheckbutton",
           background=[("active", C_PANEL)],
           indicatorcolor=[("selected", C_ACCENT), ("pressed", C_ACCENT_DK)],
           bordercolor=[("selected", C_ACCENT)])

    # 照片列表：行高够放缩略图
    st.configure("Treeview", background=C_PANEL, fieldbackground=C_PANEL,
                 foreground=C_TEXT, borderwidth=0, rowheight=56)
    st.configure("Treeview.Heading", background=C_PANEL_2, foreground=C_TEXT_DIM,
                 borderwidth=0, font=F_SMALL, padding=(8, 6), relief="flat")
    st.map("Treeview",
           background=[("selected", C_SEL)],
           foreground=[("selected", C_TEXT)])
    st.map("Treeview.Heading", background=[("active", C_PANEL_3)])

    # 账本表格：紧凑单行
    st.configure("Ledger.Treeview", background=C_PANEL, fieldbackground=C_PANEL,
                 foreground=C_TEXT, borderwidth=0, rowheight=30, font=F_SMALL)
    st.configure("Ledger.Treeview.Heading", background=C_PANEL_2,
                 foreground=C_TEXT_DIM, borderwidth=0, font=F_SMALL,
                 padding=(6, 5), relief="flat")
    st.map("Ledger.Treeview",
           background=[("selected", C_SEL)],
           foreground=[("selected", C_TEXT)])

    st.configure("TProgressbar", background=C_ACCENT, troughcolor=C_PANEL_3,
                 borderwidth=0, thickness=6, bordercolor=C_PANEL_3,
                 lightcolor=C_ACCENT, darkcolor=C_ACCENT)
    st.configure("Vertical.TScrollbar", background="#c6cedb", troughcolor=C_PANEL_2,
                 bordercolor=C_PANEL_2, arrowcolor=C_TEXT_DIM, arrowsize=13,
                 borderwidth=0, lightcolor="#c6cedb", darkcolor="#c6cedb")
    st.map("Vertical.TScrollbar", background=[("active", "#aeb9c9")])
    st.configure("Horizontal.TScrollbar", background="#c6cedb", troughcolor=C_PANEL_2,
                 bordercolor=C_PANEL_2, arrowcolor=C_TEXT_DIM, arrowsize=13,
                 borderwidth=0, lightcolor="#c6cedb", darkcolor="#c6cedb")
    st.map("Horizontal.TScrollbar", background=[("active", "#aeb9c9")])
    return st


class Divider(tk.Frame):
    def __init__(self, master, pady=8):
        super().__init__(master, bg=C_BORDER, height=1)
        self.pack(fill="x", pady=pady)


class ScrollArea(tk.Frame):
    """
    可滚动容器：把内容放进 .inner，高度不够时右侧出现细滚动条。
    用于右栏——窗口变矮时卡片不会再被切掉。
    """

    def __init__(self, master, bg=C_BG):
        super().__init__(master, bg=bg)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._cv = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._cv.grid(row=0, column=0, sticky="nsew")
        self._sb = ttk.Scrollbar(self, orient="vertical", command=self._cv.yview,
                                 style="Vertical.TScrollbar")
        self._cv.configure(yscrollcommand=self._on_scroll)

        self.inner = tk.Frame(self._cv, bg=bg)
        self._win = self._cv.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner)
        self._cv.bind("<Configure>", self._on_canvas)
        self.bind("<Enter>", self._bind_wheel)
        self.bind("<Leave>", self._unbind_wheel)

    def _on_inner(self, _):
        self._cv.configure(scrollregion=self._cv.bbox("all"))
        self._sync_bar()

    def _on_canvas(self, e):
        self._cv.itemconfigure(self._win, width=e.width)
        self._sync_bar()

    def _sync_bar(self):
        need = self.inner.winfo_reqheight() > self._cv.winfo_height()
        if need:
            self._sb.grid(row=0, column=1, sticky="ns")
        else:
            self._sb.grid_remove()
            self._cv.yview_moveto(0)

    def _on_scroll(self, first, last):
        self._sb.set(first, last)
        self._sync_bar()

    def _bind_wheel(self, _):
        self._cv.bind_all("<MouseWheel>", self._wheel)

    def _unbind_wheel(self, _):
        self._cv.unbind_all("<MouseWheel>")

    def _wheel(self, e):
        if self.inner.winfo_reqheight() <= self._cv.winfo_height():
            return
        self._cv.yview_scroll(-1 if e.delta > 0 else 1, "units")


class Field(tk.Frame):
    """带标签的输入行：标签在上，控件在下"""

    def __init__(self, master, label, widget_factory, width=None, bg=C_PANEL):
        super().__init__(master, bg=bg)
        tk.Label(self, text=label, bg=bg, fg=C_TEXT_FAINT, font=F_SMALL,
                 anchor="w").pack(fill="x")
        self.widget = widget_factory(self)
        self.widget.pack(fill="x", pady=(2, 0))
        if width:
            self.widget.configure(width=width)
