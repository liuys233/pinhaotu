# -*- coding: utf-8 -*-
"""UI 冒烟测试 + 截图：不弹窗，直接驱动一遍并截图保存"""
import os
import sys
import tempfile
import time

sys.argv = ["tiler_gui.py"]

# 本脚本在 tests/ 下，引擎与界面在项目根目录，先把根目录加进 sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import tkinter as tk
from tkinter import messagebox as mb
from PIL import Image

import tiler_core as core
import tiler_gui as G

mb.askyesno = lambda *a, **k: True
mb.showinfo = lambda *a, **k: None
mb.showwarning = lambda *a, **k: None
mb.showerror = lambda *a, **k: print("ERROR BOX:", a)

SHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")
os.makedirs(SHOT_DIR, exist_ok=True)


def shot(root, name):
    root.update()
    time.sleep(0.35)
    root.update()
    try:
        from PIL import ImageGrab
        x = root.winfo_rootx()
        y = root.winfo_rooty()
        w = root.winfo_width()
        h = root.winfo_height()
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        p = os.path.join(SHOT_DIR, name + ".png")
        img.save(p)
        print("  截图:", p, img.size)
        return p
    except Exception as e:
        print("  截图失败:", e)
        return None


def main():
    tmp = tempfile.mkdtemp(prefix="ui3_")
    paths = []
    specs = [(6000, 4000), (4000, 6000), (6000, 4000), (4000, 6000), (5000, 3333)]
    colors = [(180, 120, 90), (90, 140, 180), (140, 170, 110),
              (170, 130, 170), (120, 150, 150)]
    # 拍摄时间：前 4 张同一场（相隔 5 分钟），第 5 张隔了 6 小时 —— 用来验证
    # 照片列表「拍摄」列与画布时间跨度提示（跨度超过 SAME_SCENE_MAX_MINUTES 会变橙）
    shot_times = ["2026:05:01 10:00:00", "2026:05:01 10:05:00",
                  "2026:05:01 10:10:00", "2026:05:01 10:15:00",
                  "2026:05:01 16:30:00"]
    for i, (w, h) in enumerate(specs):
        p = os.path.join(tmp, "DSC_%04d.jpg" % (1001 + i))
        im = Image.new("RGB", (w, h), colors[i])
        # 加一点渐变让它不是纯色
        px = im.load()
        for y in range(0, h, 4):
            for x in range(0, w, 4):
                r, g, b = colors[i]
                px[x, y] = (min(255, r + y * 15 // h), g, max(0, b - x * 20 // w))
        ex = Image.Exif()
        ex[core.TAG_MAKE] = "SONY"
        ex[core.TAG_MODEL] = "ILCE-7M4"
        ex[core.TAG_DATETIME] = shot_times[i]
        ex[core.TAG_ORIENTATION] = 1
        ex.get_ifd(core.TAG_EXIF_IFD)[core.TAG_DATETIME_ORIGINAL] = shot_times[i]
        im.save(p, quality=90, exif=ex.tobytes())
        paths.append(p)

    root = tk.Tk()
    root.geometry("1400x880+60+40")
    app = G.App(root)
    root.update()
    root.lift()
    root.attributes("-topmost", True)
    root.update()
    root.attributes("-topmost", False)
    time.sleep(0.5)

    print("[1] 空状态")
    shot(root, "01_empty")

    print("[2] 加入 5 张")
    app.add_paths(paths)
    for _ in range(40):
        root.update()
        time.sleep(0.03)
    shot(root, "02_loaded")

    print("[3] 自动排版")
    app.on_layout()
    for _ in range(400):
        root.update()
        time.sleep(0.04)
        if app.plans:
            break
    print("   画布:", [(p.width, p.height, len(p.placements)) for p in app.plans])
    shot(root, "03_layout")

    print("[4] 悬停 + 拖动")
    plan = app.plans[0]
    s, ox, oy = app._pv[1], app._pv[2], app._pv[3]
    pl = plan.placements[0]
    ex = ox + (pl.x + pl.w * 0.5) * s
    ey = oy + (pl.y + pl.h * 0.5) * s
    app.on_hover(type("E", (), {"x": ex, "y": ey})())
    root.update()
    shot(root, "04_hover")

    print("[5] 导出")
    exp = os.path.join(tmp, "out")
    app.v_export.set(exp)
    app.on_export()
    for _ in range(500):
        root.update()
        time.sleep(0.05)
        if os.path.exists(exp) and any(f.endswith(".manifest.json") for f in os.listdir(exp)):
            break
    files = os.listdir(exp)
    print("   产物:", files)
    shot(root, "05_exported")

    print("[6] 切回")
    tif = [f for f in files if f.endswith(".tif")][0]
    app.v_ret.set(os.path.join(exp, tif))
    app.v_split_dir.set(os.path.join(exp, "切回原图"))
    app._refresh_steps()
    root.update()
    app.on_split()
    for _ in range(400):
        root.update()
        time.sleep(0.05)
        d = os.path.join(exp, "切回原图")
        if os.path.isdir(d) and len(os.listdir(d)) >= 5:
            break
    d = os.path.join(exp, "切回原图")
    print("   切出:", os.listdir(d) if os.path.isdir(d) else [])
    shot(root, "06_split")

    print("[7] row 版式（1:1 保真 + 超上限自动拆画布）")
    app.v_mode.set("row")
    app.plans = []                      # 清掉 grid 结果，否则等待循环会立刻退出
    app.on_layout()
    for _ in range(500):
        root.update()
        time.sleep(0.04)
        if app.plans:
            break
    mx = app.v_maxside.get()
    print("   row 画布:", [(p.width, p.height, len(p.placements)) for p in app.plans])
    assert app.plans, "row 版式没有排出画布"
    for pl in app.plans:
        assert pl.width <= mx and pl.height <= mx, f"row 画布 {pl.width}×{pl.height} 超单边上限"
        for p in pl.placements:
            s = app.srcs[p.src_index]
            assert (p.w, p.h) == (s.w, s.h), f"row 版式把「{s.name}」缩放了（应 1:1）"
    shot(root, "07_row")

    # 账本
    app.refresh_ledger()
    root.update()
    shot(root, "08_ledger")

    print("\nUI SMOKE PASS")
    print("截图目录:", SHOT_DIR)
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
