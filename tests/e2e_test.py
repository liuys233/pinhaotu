# -*- coding: utf-8 -*-
"""
端到端测试：不启动 GUI，直接跑「排版 → 导出 → 模拟像素蛋糕导出 → 切回」全链路，
并比对像素。

用法：python tests/e2e_test.py（在项目根目录下执行）
"""
import os
import sys
import json
import random
import shutil
import tempfile

from PIL import Image, ImageDraw, ImageFont

# 本脚本在 tests/ 下，引擎在项目根目录，先把根目录加进 sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import tiler_core as core

MAX_SIDE = 12000
GUTTER = 12


def make_sources(tmp, specs):
    paths = []
    rng = random.Random(42)
    for i, (w, h, tag) in enumerate(specs):
        im = Image.new("RGB", (w, h), (24, 26, 30))
        d = ImageDraw.Draw(im)
        # 方向标记：左上红、右上绿、左下蓝、右下黄
        s = max(10, min(w, h) // 12)
        d.rectangle((0, 0, s, s), fill=(255, 40, 40))
        d.rectangle((w - s, 0, w, s), fill=(40, 255, 40))
        d.rectangle((0, h - s, s, h), fill=(40, 40, 255))
        d.rectangle((w - s, h - s, w, h), fill=(255, 230, 40))
        # 高频纹理（检测重采样）
        for _ in range(h * w // 400):
            x, y = rng.randrange(w), rng.randrange(h)
            v = 255 if rng.random() > 0.5 else 0
            im.putpixel((x, y), (v, v, v))
        # 1px 网格线
        for x in range(0, w, 7):
            for y in range(0, h, 3):
                im.putpixel((x, y), (255, 255, 255))
        p = os.path.join(tmp, f"{tag}.jpg")
        im.save(p, quality=98, subsampling=0)
        paths.append(p)
    return paths


def max_abs_diff(a: Image.Image, b: Image.Image) -> int:
    if a.size != b.size:
        return 9999
    import numpy as np
    x = np.asarray(a.convert("RGB"), dtype=np.int16)
    y = np.asarray(b.convert("RGB"), dtype=np.int16)
    return int(np.abs(x - y).max())


def main():
    tmp = tempfile.mkdtemp(prefix="pct_e2e_")
    print("工作目录:", tmp)

    specs = [
        (6000, 4000, "A_横"),
        (4000, 6000, "B_竖"),
        (5000, 3000, "C_横"),
        (3000, 4500, "D_竖"),
        (2000, 2000, "E_方"),
    ]
    paths = make_sources(tmp, specs)
    srcs = []
    for p in paths:
        im = Image.open(p)
        srcs.append(core.SourceImage(
            path=p, name=os.path.basename(p),
            natural_w=im.width, natural_h=im.height, format="jpeg",
            sha256=core.sha256_file(p)))

    # ---------- 1) 排版 ----------
    plans = core.pack_canvases(srcs, MAX_SIDE, gap=0, gutter=GUTTER)
    print(f"\n[1] 排版: {len(srcs)} 张 -> {len(plans)} 张画布")
    for pl in plans:
        print(f"    画布{pl.index+1}: {pl.width}×{pl.height}  "
              f"{len(pl.placements)} 张  填充 {pl.fill()*100:.1f}%")
        assert pl.width <= MAX_SIDE and pl.height <= MAX_SIDE, "超出单边上限"
        for i in range(len(pl.placements)):
            for j in range(i + 1, len(pl.placements)):
                a, b = pl.placements[i], pl.placements[j]
                assert (a.x + a.w <= b.x or b.x + b.w <= a.x or
                        a.y + a.h <= b.y or b.y + b.h <= a.y), "出现重叠"
    assert len(plans) >= 1

    export_dir = os.path.join(tmp, "像素拼图导出")
    os.makedirs(export_dir, exist_ok=True)
    batch = "e2e" + str(random.randrange(9999)).zfill(4)

    # ---------- 2) 导出 ----------
    print("\n[2] 合成导出")
    manifests = []
    for pl in plans:
        out = os.path.join(export_dir, f"TILE_{batch}_c{pl.index+1:02d}.tif")
        core.compose_canvas(pl, srcs, out, gutter=GUTTER)
        man = core.build_manifest(pl, srcs, out, GUTTER, 0, batch, False, MAX_SIDE)
        core.write_manifest(man, out)
        manifests.append((out, man))
        print(f"    {os.path.basename(out)}  {pl.width}×{pl.height}  {core.human_size(os.path.getsize(out))}")

    # ---------- 3) 原样切回（最关键：验证 1:1 无损） ----------
    print("\n[3] 原样切回（不做任何精修）")
    worst = 0
    for out, man in manifests:
        back = os.path.join(tmp, "切回原图", os.path.basename(out)[:-4])
        rep = core.split_canvas(out, man, back, fmt="png", keep_exif=False)
        assert rep["dimsMatch"], f"尺寸不匹配: {rep['actual']} vs {rep['expected']}"
        for o in rep["outputs"]:
            item = man["items"][o["index"]]
            a = Image.open(o["file"]).convert("RGB")
            b = Image.open(item["source"]).convert("RGB")
            d = max_abs_diff(a, b)
            worst = max(worst, d)
            print(f"    {o['name']:<22} {str(a.size):<14} 最大像素偏差 = {d}")
    print(f"    >>> 全局最大偏差 = {worst}")
    assert worst == 0, "原样切回必须逐像素一致"

    # ---------- 4) 模拟像素蛋糕导出 ----------
    print("\n[4] 模拟像素蛋糕：调色后导出 TIFF，尺寸不变")
    edited = []
    for out, man in manifests:
        im = Image.open(out).convert("RGB")
        # 只做色彩类调整（模拟"整体曝光+对比+暖色"）
        px = im.load()
        wdt, hgt = im.size
        for y in range(0, hgt, 3):
            for x in range(0, wdt, 3):
                r, g, b = px[x, y]
                px[x, y] = (min(255, int(r * 1.06 + 6)),
                            min(255, int(g * 1.02 + 3)),
                            min(255, int(b * 0.98)))
        e = out.replace("TILE_", "EDITED_TILE_")
        im.save(e, "TIFF", compression="tiff_lzw")
        edited.append((e, man))

    print("\n[5] 从精修图切回（应只带色彩变化，几何必须精确对齐）")
    for e, man in edited:
        back = os.path.join(tmp, "切回原图_精修", os.path.basename(e)[:-4])
        rep = core.split_canvas(e, man, back, fmt="png", keep_exif=False)
        for o in rep["outputs"]:
            item = man["items"][o["index"]]
            a = Image.open(o["file"]).convert("RGB")
            b = Image.open(item["source"]).convert("RGB")
            assert a.size == b.size, f"{o['name']} 尺寸 {a.size} != 原图 {b.size}"
            # 几何对齐用结构验证：四角标记位置必须与原图一致
            import numpy as np
            x = np.asarray(a, dtype=np.float32)
            y = np.asarray(b, dtype=np.float32)
            # 色调变了，直接比像素没意义；用「亮度结构」做相关系数（对亮度偏移不敏感）
            xl = x.mean(axis=2).reshape(-1)
            yl = y.mean(axis=2).reshape(-1)
            xl = xl - xl.mean()
            yl = yl - yl.mean()
            den = (float(np.sqrt((xl * xl).sum())) * float(np.sqrt((yl * yl).sum())))
            corr = float((xl * yl).sum() / den) if den > 1e-6 else 0.0
            print(f"    {o['name']:<22} {str(a.size):<14} 结构相关 = {corr:.6f}")
            assert corr > 0.99, "几何没对齐"

    # ---------- 6) 缩放后切回（模拟导出被缩放） ----------
    print("\n[6] 模拟像素蛋糕导出被整体缩放 75%")
    out, man = manifests[0]
    im = Image.open(out).convert("RGB")
    small = im.resize((int(im.width * 0.75), int(im.height * 0.75)), Image.LANCZOS)
    sp = os.path.join(tmp, "SCALED_" + os.path.basename(out))
    small.save(sp, "TIFF", compression="tiff_lzw")
    rep = core.split_canvas(sp, man, os.path.join(tmp, "切回_缩放"), fmt="png", keep_exif=False)
    print("    warnings:", rep["warnings"])
    for o in rep["outputs"][:3]:
        a = Image.open(o["file"])
        print(f"    {o['name']:<22} {a.size}")

    # ---------- 7) 宽高比变化必须拒绝 ----------
    print("\n[7] 宽高比被改动时必须拒绝")
    bad = im.crop((0, 0, im.width, int(im.height * 0.9)))
    bp = os.path.join(tmp, "CROPPED_" + os.path.basename(out))
    bad.save(bp, "TIFF", compression="tiff_lzw")
    try:
        core.split_canvas(bp, man, os.path.join(tmp, "切回_坏"), keep_exif=False)
        print("    !! 没有拒绝，这是 bug")
        return 1
    except ValueError as e:
        print("    ✔ 已拒绝:", e)

    # ---------- 8) EXIF 往返：非 JPEG 源图 + 三种输出格式 ----------
    print("\n[8] EXIF 往返（TIFF/PNG 源图，jpeg/tiff/png 输出）")
    ex_paths = []
    for tag, ext, pilfmt, model, when in [
            ("F_索尼", ".jpg", "JPEG", "ILCE-7M4", "2026:05:01 10:20:30"),
            ("G_索尼", ".tif", "TIFF", "ILCE-7RM5", "2026:05:01 10:25:30"),
            ("H_佳能", ".png", "PNG", "Canon EOS R5", "2026:05:01 10:31:00")]:
        im2 = Image.new("RGB", (2400, 1600), (60, 70, 80))
        d = ImageDraw.Draw(im2)
        d.rectangle((0, 0, 120, 120), fill=(255, 40, 40))
        ex = Image.Exif()
        ex[core.TAG_MAKE] = "SONY"
        ex[core.TAG_MODEL] = model
        ex[core.TAG_DATETIME] = when
        ex[core.TAG_ORIENTATION] = 1
        sub = ex.get_ifd(core.TAG_EXIF_IFD)
        sub[core.TAG_DATETIME_ORIGINAL] = when
        sub[core.TAG_LENS_MODEL] = "FE 24-70mm F2.8 GM II"
        p = os.path.join(tmp, tag + ext)
        im2.save(p, pilfmt, exif=ex.tobytes())
        ex_paths.append(p)

    ex_srcs = []
    for p in ex_paths:
        w, h, pf = core.probe_image(p)
        s = core.SourceImage(path=p, name=os.path.basename(p),
                             natural_w=w, natural_h=h, format=pf,
                             sha256=core.sha256_file(p))
        core.attach_exif(s)
        ex_srcs.append(s)
        print("    提取 %-14s 拍摄时间=%s 机型=%s EXIF=%d 字节"
              % (s.name, s.shot_time or "-", s.model or "-", len(s.exif_b64)))

    for s in ex_srcs:
        assert s.shot_time, f"{s.name} 读不到拍摄时间"
        assert s.model, f"{s.name} 读不到机型"
        assert s.exif_b64, f"{s.name} 提取不到 EXIF 块"

    ex_plans = core.pack_canvases(ex_srcs, MAX_SIDE, gap=0, gutter=GUTTER)
    ex_export = os.path.join(tmp, "EXIF导出")
    os.makedirs(ex_export, exist_ok=True)
    for pl in ex_plans:
        cfile = os.path.join(ex_export, f"EXIF_c{pl.index+1:02d}.tif")
        core.compose_canvas(pl, ex_srcs, cfile, gutter=GUTTER)
        eman = core.build_manifest(pl, ex_srcs, cfile, GUTTER, 0, batch + "x",
                                   False, MAX_SIDE)
        core.write_manifest(eman, cfile)
        assert eman["version"] == 5, "manifest 版本应为 5"
        assert all(it["exifB64"] for it in eman["items"]), "manifest 未内嵌 EXIF"

        # 把源图移走，验证「原图不在也能写回」
        moved = os.path.join(tmp, "归档")
        os.makedirs(moved, exist_ok=True)
        for it in eman["items"]:
            shutil.move(it["source"], os.path.join(moved, os.path.basename(it["source"])))

        for fmt in ("jpeg", "tiff", "png"):
            back = os.path.join(tmp, "EXIF切回_%s" % fmt, os.path.basename(cfile)[:-4])
            rep = core.split_canvas(cfile, eman, back, fmt=fmt)
            for o in rep["outputs"]:
                assert o.get("file"), f"{fmt} 切分失败: {o.get('error')}"
                tags = core.read_exif_tags(o["file"])
                assert tags["make"] == "SONY", f"{fmt}/{o['name']} 厂商丢失: {tags}"
                assert tags["model"], f"{fmt}/{o['name']} 机型丢失"
                assert tags["shotTime"], f"{fmt}/{o['name']} 拍摄时间丢失"
                assert o.get("exifCopied"), f"{fmt}/{o['name']} 未写回 EXIF"
                with Image.open(o["file"]) as chk:
                    assert chk.getexif().get(core.TAG_ORIENTATION) in (None, 1), \
                        f"{fmt}/{o['name']} Orientation 未归一"
                    if fmt in ("jpeg", "png"):
                        lens = chk.getexif().get_ifd(core.TAG_EXIF_IFD).get(core.TAG_LENS_MODEL)
                        assert lens, f"{fmt}/{o['name']} ExifIFD 镜头未带出"
            if fmt == "tiff":
                note = [w for w in rep["warnings"] if "ExifIFD" in w]
                assert note, "TIFF 输出应明确提示子目录信息未能带出"
            print("    %-5s ✔ 完整/IFD0" % fmt)

    # ---------- 9) row / col：按原始像素排布 + 超上限自动拆画布 ----------
    print("\n[9] row/col 1:1 保真排布（混排）+ 超上限自动拆画布")
    for axis in ("row", "col"):
        s_plans = core.pack_strip(srcs, MAX_SIDE, axis=axis, gap=0, gutter=GUTTER)
        print(f"    {axis}: {len(srcs)} 张 -> {len(s_plans)} 画布")
        worst_s = 0
        for pl in s_plans:
            assert pl.width <= MAX_SIDE and pl.height <= MAX_SIDE, \
                f"{axis} 画布 {pl.width}×{pl.height} 超单边上限"
            for p in pl.placements:
                s = srcs[p.src_index]
                assert (p.w, p.h) == (s.w, s.h), \
                    f"{axis} 「{s.name}」占位 {p.w}×{p.h} ≠ 原图 {s.w}×{s.h}（被缩放）"
            for i in range(len(pl.placements)):
                for j in range(i + 1, len(pl.placements)):
                    a, b = pl.placements[i], pl.placements[j]
                    assert (a.x + a.w <= b.x or b.x + b.w <= a.x or
                            a.y + a.h <= b.y or b.y + b.h <= a.y), f"{axis} 出现重叠"
            print(f"      画布{pl.index+1}: {pl.width}×{pl.height}  "
                  f"{len(pl.placements)} 张  填充 {pl.fill()*100:.1f}%")
            cf = os.path.join(tmp, "STRIP_%s_c%02d.tif" % (axis, pl.index + 1))
            core.compose_canvas(pl, srcs, cf, gutter=GUTTER)
            sman = core.build_manifest(pl, srcs, cf, GUTTER, 0, batch + "s",
                                       False, MAX_SIDE)
            core.write_manifest(sman, cf)
            srep = core.split_canvas(
                cf, sman,
                os.path.join(tmp, "STRIP切回", "%s_c%02d" % (axis, pl.index + 1)),
                fmt="png", keep_exif=False)
            assert srep["dimsMatch"], f"{axis} 尺寸不匹配"
            for it, o in zip(sman["items"], srep["outputs"]):
                a = Image.open(o["file"]).convert("RGB")
                b = Image.open(it["source"]).convert("RGB")
                worst_s = max(worst_s, max_abs_diff(a, b))
        print(f"      >>> {axis} 全局最大偏差 = {worst_s}")
        assert worst_s == 0, f"{axis} 原样切回必须逐像素一致"

    # 6 张 2000×1500 横排，单边上限 5000 → 每画布 2 张、共 3 画布
    fakes = [core.SourceImage(path=os.path.join(tmp, "f%d.jpg" % i),
                              name="f%d" % i, natural_w=2000, natural_h=1500)
             for i in range(6)]
    sp = core.pack_strip(fakes, 5000, axis="row", gap=0, gutter=GUTTER)
    assert len(sp) == 3, f"应自动拆成 3 张画布，实得 {len(sp)}"
    assert all(p.width <= 5000 and p.height <= 5000 for p in sp), "拆出的画布超上限"
    try:
        core.pack_strip([core.SourceImage(path=os.path.join(tmp, "big.jpg"),
                                          name="big", natural_w=20000,
                                          natural_h=1500)],
                        5000, axis="row", gutter=GUTTER)
        raise AssertionError("单张超上限时应报错")
    except ValueError as e:
        print("    ✔ 单张超上限已拒绝:", e)
    print("    ✔ 自动拆画布：6 张 2000×1500 → 3 画布（每画布 2 张）")

    print("\n===== E2E PASS =====")
    print("产物目录:", tmp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
