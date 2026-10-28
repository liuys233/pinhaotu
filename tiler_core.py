# -*- coding: utf-8 -*-
"""
拼好图（pinhaotu） for Windows — 核心引擎  v2
=========================================
针对「像素蛋糕适配」+「真实相机尺寸适配」重写：

1) 相机预设：内置 30+ 主流机型真实尺寸，直接算「一组能拼几张、省多少」。
2) 三级上限：hardLimit 13000（导入硬闸门）/ safeLimit 12000（推荐）/ textureLimit 16384
   （GPU 纹理：超了只静默降采样，不是导入门槛）。
3) 排版 v3：朝向感知列带 + 货架 + MaxRects × 8 排序键 + 24 随机起点 + 紧包围盒重压
   + 多起点分组 + 画布合并 + 搬空重填。**默认零旋转**（人脸朝上），旋转仅作可选增益。
4) 像素蛋糕约束：最外层离画布边缘 ≥ gutter；保护带四边四角铺自身镜像；
   排版一律按 (w+2g) × (h+2g) 占位，保证相邻两张的保护带互不侵占。
5) 切分：尺寸/宽高比校验、旋转还原、EXIF 搬移、单张失败隔离、重名不覆盖。

参考自 wjunfei88-maker/pixcake-tiler 的 M0 实测结论。
无 GUI 依赖。
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import random
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageOps, TiffImagePlugin

Image.MAX_IMAGE_PIXELS = None

APP_SIGNATURE = "pinhaotu"
MANIFEST_VERSION = 5      # v4 -> v5：item 增加 shotTime / model / exifB64 / exifKind

# ==========================================================================
# 像素蛋糕适配常量
# ==========================================================================
# 导入硬闸门：像素蛋糕主流程校验 `cmp eax, 0x32c8`（13000），单边 >13000 直接弹
# 「暂时只支持导入长宽在100~13000像素区间的图片」硬拒 —— 连静默降采样的机会都没有。
# 拼好的 TIF 必须先「进项目」才能精修，所以这是真正的上限，必须是 13000 而不是 16384。
PIXCAKE_HARD_LIMIT = 13000
# 推荐值：与官方素材链路的 12000 门一致，留 1000px 余量给保护带与多张图的零头。
PIXCAKE_SAFE_LIMIT = 12000
# 导入下限：同一条校验文案「暂时只支持导入长宽在100~13000像素区间的图片」，
# 单边 <100px 同样会被硬拒。画布过小通常是因为塞了极小尺寸的照片。
PIXCAKE_MIN_SIDE = 100
# 画布文件大小上限：拖入画布/图层面板限 1GB，超了拒绝导入。
PIXCAKE_MAX_FILE_BYTES = 1 << 30
# GPU 纹理上限（glGetIntegerv(GL_MAX_TEXTURE_SIZE)，随显卡变）。超过只是被静默降分辨率，
# 不是导入门槛 —— 不要拿它当上限用，否则排出来的画布会在导入时整批失败。
PIXCAKE_TEXTURE_LIMIT = 16384
PIXCAKE_RECOMMENDED_GUTTER = 12  # 保护带半宽（M0：跨缝残余位移 ≤ 1px）
PIXCAKE_EXPORT_FORMAT = "TIFF"

# ==========================================================================
# EXIF tag / 同场同光判定
# ==========================================================================
TAG_THUMB_OFFSET = 0x0201        # IFD1：缩略图偏移（要摘掉）
TAG_THUMB_LENGTH = 0x0202        # IFD1：缩略图长度
TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
TAG_ORIENTATION = 0x0112
TAG_SOFTWARE = 0x0131
TAG_DATETIME = 0x0132
TAG_EXIF_IFD = 0x8769            # ExifIFD 子目录
TAG_GPS_IFD = 0x8825
TAG_DATETIME_ORIGINAL = 0x9003   # 在 ExifIFD 里，不在 IFD0
TAG_LENS_MODEL = 0xA434          # 同上

SAME_SCENE_MAX_MINUTES = 30      # 同场同光启发式阈值：画布内拍摄时间跨度超过它就提示

# ==========================================================================
# 相机预设
# ==========================================================================
CAMERA_PRESETS: Dict[str, Dict[str, int]] = {
    "Sony A1 (50MP)":              {"w": 8640, "h": 5760},
    "Sony A7 IV (33MP)":           {"w": 7008, "h": 4672},
    "Sony A7R III/IV (42MP)":      {"w": 7952, "h": 5304},
    "Sony A7R V (61MP)":           {"w": 9504, "h": 6336},
    "Sony A7S III (12MP)":         {"w": 4240, "h": 2832},
    "Sony A7C II (33MP)":          {"w": 7008, "h": 4672},
    "Sony A6400/A6600 (24MP)":     {"w": 6000, "h": 4000},
    "Canon R5 (45MP)":             {"w": 8192, "h": 5464},
    "Canon R5 II (45MP)":          {"w": 8192, "h": 5464},
    "Canon R6 II (24MP)":          {"w": 6000, "h": 4000},
    "Canon R (30MP)":              {"w": 6720, "h": 4480},
    "Canon 5D IV (30MP)":          {"w": 6720, "h": 4480},
    "Nikon Z8 (46MP)":             {"w": 8256, "h": 5504},
    "Nikon Z9 (46MP)":             {"w": 8256, "h": 5504},
    "Nikon Z6 II (24MP)":          {"w": 6048, "h": 4024},
    "Nikon D850 (46MP)":           {"w": 8256, "h": 5504},
    "Fujifilm GFX100/100S (102MP)": {"w": 11648, "h": 8736},
    "Fujifilm GFX100 II (102MP)":  {"w": 11648, "h": 8736},
    "Fujifilm X-T5/X-H2 (40MP)":   {"w": 7728, "h": 5152},
    "Fujifilm X-T4 (26MP)":        {"w": 6240, "h": 4160},
    "Panasonic S5 II (24MP)":      {"w": 6000, "h": 4000},
    "Panasonic GH6 (25MP)":        {"w": 5776, "h": 4336},
    "Leica SL2 (47MP)":            {"w": 8368, "h": 5584},
    "Leica Q3 (60MP)":             {"w": 9520, "h": 6336},
    "Hasselblad X2D (100MP)":      {"w": 11664, "h": 8750},
    "Phase One IQ4 (151MP)":       {"w": 14204, "h": 10652},
    "小图 3000×2000":               {"w": 3000, "h": 2000},
    "小图 2000×2000":               {"w": 2000, "h": 2000},
}


def orientation_of(w: int, h: int) -> str:
    """照片真实朝向：P=竖（人像，脸朝上）/ L=横 / S=方。

    排版默认零旋转，所以「真实朝向」就是「人脸朝上的朝向」。竖图竖排、横图横排，
    不转 90° 才能保证修人脸时五官是正的。"""
    if h > w:
        return "P"
    if w > h:
        return "L"
    return "S"


def capacity_report(w: int, h: int, max_side: int = PIXCAKE_SAFE_LIMIT,
                    gutter: int = PIXCAKE_RECOMMENDED_GUTTER,
                    gap: int = 0) -> Dict[str, Any]:
    """给定照片尺寸，算「一张画布最多能放几张」（几何上界，用于界面提示）。

    与排版器同一套占位模型（内容 w×h，块 (w+2g)×(h+2g)，外层再留 gutter）：
      - 竖向排：列宽 = 短边 + 2g，列内竖叠 floor((max_side-g)/(长边+2g)) 张
      - 横向排：列宽 = 长边 + 2g，列内竖叠 floor((max_side-g)/(短边+2g)) 张
      - 混排  ：把画布宽度切成若干竖列 + 若干横列，枚举组合取最大张数
        （A7M4：1 竖列 + 1 横列 = 11728px ≤ 12000 → 1 + 2 = 3 张）
    """
    g = gutter
    long_, short_ = max(w, h), min(w, h)
    bw_l, bh_l = long_ + 2 * g, short_ + 2 * g     # 横放：块宽用长边
    bw_p, bh_p = short_ + 2 * g, long_ + 2 * g     # 竖放：块宽用短边

    def axis(block_w, block_h):
        # 块 + 间隙沿单边铺满即可：画布尺寸 = 最后一块的右/下边界，不再额外留 gutter
        cols = max(0, int((max_side + gap) // (block_w + gap)))
        rows = max(0, int((max_side + gap) // (block_h + gap)))
        return cols, rows, cols * rows

    lc, lr, land = axis(bw_l, bh_l)
    pc, pr, port = axis(bw_p, bh_p)

    # 混排：枚举「n_p 个竖列 + n_l 个横列」，宽度受限，取张数最大
    mixed = max(land, port)
    for n_p in range(0, min(pc, 6) + 1):
        for n_l in range(0, min(lc, 6) + 1):
            if n_p + n_l == 0:
                continue
            if n_p * bw_p + n_l * bw_l + (n_p + n_l - 1) * gap > max_side:
                continue
            mixed = max(mixed, n_p * pr + n_l * lr)

    return {
        "landscape": {"cols": lc, "rows": lr, "total": land},
        "portrait": {"cols": pc, "rows": pr, "total": port},
        "mixed": mixed,
        "canvasMaxSide": max_side,
        "gutter": gutter,
    }


def detect_camera(w: int, h: int, tol: int = 8) -> str:
    """按尺寸猜机型（容忍 ±tol px 的裁切差异）。EXIF 不可用时的兜底。"""
    for name, s in CAMERA_PRESETS.items():
        if (abs(w - s["w"]) <= tol and abs(h - s["h"]) <= tol) or \
           (abs(w - s["h"]) <= tol and abs(h - s["w"]) <= tol):
            return name
    return ""


# EXIF Model 串 -> CAMERA_PRESETS 的 key。
# 匹配规则是「最长命中优先」，所以像 R5 与 R5m2 这种包含关系必须让更长的串胜出。
CAMERA_MODEL_HINTS: Tuple[Tuple[str, str], ...] = (
    ("ILCE-7RM5", "Sony A7R V (61MP)"),
    ("ILCE-7RM4", "Sony A7R III/IV (42MP)"),
    ("ILCE-7RM3", "Sony A7R III/IV (42MP)"),
    ("ILCE-7SM3", "Sony A7S III (12MP)"),
    ("ILCE-7CM2", "Sony A7C II (33MP)"),
    ("ILCE-7M4", "Sony A7 IV (33MP)"),
    ("ILCE-1", "Sony A1 (50MP)"),
    ("ILCE-6600", "Sony A6400/A6600 (24MP)"),
    ("ILCE-6400", "Sony A6400/A6600 (24MP)"),
    ("R5M2", "Canon R5 II (45MP)"),
    ("R5 MARK II", "Canon R5 II (45MP)"),
    ("R5", "Canon R5 (45MP)"),
    ("R6M2", "Canon R6 II (24MP)"),
    ("R6 MARK II", "Canon R6 II (24MP)"),
    ("5D MARK IV", "Canon 5D IV (30MP)"),
    ("EOS R", "Canon R (30MP)"),
    ("Z 8", "Nikon Z8 (46MP)"),
    ("Z 9", "Nikon Z9 (46MP)"),
    ("Z 6_2", "Nikon Z6 II (24MP)"),
    ("D850", "Nikon D850 (46MP)"),
    ("GFX100 II", "Fujifilm GFX100 II (102MP)"),
    ("GFX 100 II", "Fujifilm GFX100 II (102MP)"),
    ("GFX100S", "Fujifilm GFX100/100S (102MP)"),
    ("GFX 100S", "Fujifilm GFX100/100S (102MP)"),
    ("GFX100", "Fujifilm GFX100/100S (102MP)"),
    ("X-T5", "Fujifilm X-T5/X-H2 (40MP)"),
    ("X-H2", "Fujifilm X-T5/X-H2 (40MP)"),
    ("X-T4", "Fujifilm X-T4 (26MP)"),
    ("DC-S5M2", "Panasonic S5 II (24MP)"),
    ("DC-GH6", "Panasonic GH6 (25MP)"),
    ("LEICA SL2", "Leica SL2 (47MP)"),
    ("LEICA Q3", "Leica Q3 (60MP)"),
    ("X2D", "Hasselblad X2D (100MP)"),
    ("IQ4", "Phase One IQ4 (151MP)"),
)


def detect_camera_exif(make: str = "", model: str = "") -> str:
    """按 EXIF 的 Make/Model 识别机型。命中不到返回空串，由调用方回落尺寸猜。

    取「出现位置最靠后」的那条：型号标识通常在串尾，而像 "EOS R" 这种泛称会出现在
    "EOS R5" 前面。若按最长优先，"CANON EOS R5" 会被 "EOS R" 抢走而误判成 Canon R。
    同位置再比长度，用来区分 "R5" 与 "R5 MARK II"。"""
    text = ("%s %s" % (make or "", model or "")).upper()
    if not text.strip():
        return ""
    best_pos, best_len, best = -1, 0, ""
    for needle, preset in CAMERA_MODEL_HINTS:
        up = needle.upper()
        pos = text.rfind(up)
        if pos < 0:
            continue
        if pos > best_pos or (pos == best_pos and len(up) > best_len):
            best_pos, best_len, best = pos, len(up), preset
    return best


# ==========================================================================
# 工具
# ==========================================================================
def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


# ==========================================================================
# EXIF
# ==========================================================================
def _iter_jpeg_segments(data: bytes):
    if data[:2] != b"\xff\xd8":
        return
    i, n = 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            return
        marker = data[i + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        yield marker, i, seg_len, i + 4, seg_len - 2
        if marker == 0xDA:
            return
        i += 2 + seg_len


def extract_exif_app1(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if data[:2] != b"\xff\xd8":
        return None
    for marker, start, seg_len, ps, plen in _iter_jpeg_segments(data):
        if marker == 0xE1 and plen >= 6 and data[ps:ps + 6] == b"Exif\x00\x00":
            return data[start:start + 2 + seg_len]
    return None


def _strip_thumb_normalize(app1: bytes) -> bytes:
    try:
        buf = bytearray(app1)
        t = 10  # \xff\xe1 + len(2) + "Exif\0\0"
        if bytes(buf[t:t + 2]) == b"II":
            e = "<"
        elif bytes(buf[t:t + 2]) == b"MM":
            e = ">"
        else:
            return app1
        ifd0 = struct.unpack(e + "I", buf[t + 4:t + 8])[0]

        def rd(o, f):
            sz = struct.calcsize(f)
            return struct.unpack(e + f, buf[t + o:t + o + sz])[0]

        def wr(o, f, v):
            sz = struct.calcsize(f)
            buf[t + o:t + o + sz] = struct.pack(e + f, v)

        count = rd(ifd0, "H")
        nxt = ifd0 + 2 + count * 12
        if nxt + 4 <= len(buf) - t:
            wr(nxt, "I", 0)
        for i in range(count):
            ent = ifd0 + 2 + i * 12
            if rd(ent, "H") == 0x0112:
                typ = rd(ent + 2, "H")
                if typ == 3:
                    wr(ent + 8, "H", 1)
                elif typ == 4:
                    wr(ent + 8, "I", 1)
                break
        return bytes(buf)
    except Exception:
        return app1


def _exif_str(v) -> str:
    """EXIF 值转可读字符串（可能是 bytes / str / 数字 / IFDRational）"""
    if v is None:
        return ""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace").strip("\x00").strip()
    return str(v).strip("\x00").strip()


def _exif_lookup(ex, tag: int):
    """先查 IFD0，再查 ExifIFD 子目录。
    真实相机把 DateTimeOriginal / LensModel 放在 ExifIFD 里，直接 .get() 拿到的是 None。"""
    try:
        v = ex.get(tag)
    except Exception:
        v = None
    if v is not None:
        return v
    try:
        return ex.get_ifd(TAG_EXIF_IFD).get(tag)
    except Exception:
        return None


def read_exif_tags(path: str) -> Dict[str, str]:
    """读拍摄相关信息。JPEG / TIFF / PNG 通用，读不到就返回空串，绝不抛异常。"""
    out = {"make": "", "model": "", "lens": "", "software": "", "shotTime": ""}
    try:
        with Image.open(path) as im:
            ex = im.getexif()
            if not ex:
                return out
            out["make"] = _exif_str(_exif_lookup(ex, TAG_MAKE))
            out["model"] = _exif_str(_exif_lookup(ex, TAG_MODEL))
            out["lens"] = _exif_str(_exif_lookup(ex, TAG_LENS_MODEL))
            out["software"] = _exif_str(_exif_lookup(ex, TAG_SOFTWARE))
            out["shotTime"] = (_exif_str(_exif_lookup(ex, TAG_DATETIME_ORIGINAL))
                               or _exif_str(_exif_lookup(ex, TAG_DATETIME)))
    except Exception:
        pass
    return out


def _normalize_exif_object(ex) -> Optional[bytes]:
    """Pillow Exif 对象归一后序列化成裸 EXIF 块（自带 Exif\\0\\0 头）：
    - Orientation 归 1：方向已烤进像素，再写回 Orientation 会让看图软件转第二次 → 竖拍躺倒
    - 摘掉缩略图指针：否则资源管理器显示的是没修过的旧缩略图"""
    try:
        ex[TAG_ORIENTATION] = 1
        ex.pop(TAG_THUMB_OFFSET, None)
        ex.pop(TAG_THUMB_LENGTH, None)
    except Exception:
        pass
    try:
        return ex.tobytes() or None
    except Exception:
        return None


def extract_exif_blob(path: str) -> Tuple[Optional[bytes], str]:
    """
    统一提取入口。返回 (blob, kind)：
      "app1" → 完整 APP1 段（含 \\xff\\xe1 + 长度），用于往 JPEG 里塞
      "raw"  → 裸 EXIF 块（自带 Exif\\0\\0 头），用于 Pillow 的 exif= 参数
      ""     → 没提取到

    JPEG 走原来的手写字节通道（原样保住厂商私有 MakerNote，已被生产验证）；
    其它格式（TIFF / PNG / WEBP）走 Pillow 原生通道 —— 这正是原先丢失 EXIF 的地方。
    """
    seg = extract_exif_app1(path)
    if seg:
        return _strip_thumb_normalize(seg), "app1"

    try:
        with Image.open(path) as im:
            ex = im.getexif()
            if not ex:
                return None, ""
            # 强制加载 ExifIFD / GPS 子目录，否则 tobytes() 可能漏掉它们
            for sub in (TAG_EXIF_IFD, TAG_GPS_IFD):
                try:
                    ex.get_ifd(sub)
                except Exception:
                    pass
            blob = _normalize_exif_object(ex)
    except Exception:
        return None, ""

    if not blob or len(blob) <= 8:
        return None, ""
    return blob, "raw"


def exif_payload(blob: bytes, kind: str) -> bytes:
    """统一成裸 EXIF 块，供 tiff / png / webp 的 save(exif=...) 使用"""
    if kind == "app1":
        return blob[4:] if blob[:2] == b"\xff\xe1" else blob
    return blob


def exif_segment(blob: bytes, kind: str) -> Optional[bytes]:
    """统一成完整 APP1 段，供 JPEG 段注入使用。超出段长上限返回 None。"""
    payload = exif_payload(blob, kind)
    if not payload or payload[:6] != b"Exif\x00\x00":
        return None
    n = len(payload) + 2
    if n > 0xFFFF:
        return None
    return b"\xff\xe1" + struct.pack(">H", n) + payload


def exif_blob_for_item(it: Dict[str, Any]) -> Tuple[Optional[bytes], str]:
    """切分时取该条记录的 EXIF：manifest 内嵌优先，回落到源文件路径。
    内嵌的意义是「原图归档 / 改名 / 移走后照样写得回」。"""
    b64 = it.get("exifB64") or ""
    if b64:
        try:
            return base64.b64decode(b64), (it.get("exifKind") or "raw")
        except Exception:
            pass
    sp = it.get("source") or ""
    if sp and os.path.exists(sp):
        return extract_exif_blob(sp)
    return None, ""


def save_exif_kwargs(blob: Optional[bytes], kind: str, outfmt: str) -> Dict[str, Any]:
    """保存阶段要并入的 kwargs：png / webp 由 Pillow 直接写 exif chunk（完整 EXIF 可用）"""
    if blob and kind and outfmt in ("png", "webp"):
        return {"exif": exif_payload(blob, kind)}
    return {}


# TIFF 只能安全写 IFD0。ExifIFD 子目录（DateTimeOriginal / LensModel / ISO / 光圈 / 快门）
# Pillow 的 TIFF 编码器写不进去：libtiff 会以 "Bad ... value ... EXIFIFDOffset" 直接报错，
# 或把 0x8769 写成无效偏移（实测 Pillow 12.2.0：exif= / tiffinfo 传嵌套 IFD 均失败）。
TIFF_IFD0_TAGS = (TAG_MAKE, TAG_MODEL, TAG_SOFTWARE, TAG_DATETIME, TAG_ORIENTATION)


def tiff_ifd0(blob: Optional[bytes], kind: str):
    """为 TIFF 输出构造 IFD0（只放 Pillow 能可靠写入的 tag），供 save(tiffinfo=...) 使用"""
    if not blob or not kind:
        return None
    try:
        ex = Image.Exif()
        ex.load(exif_payload(blob, kind))
    except Exception:
        return None
    info = TiffImagePlugin.ImageFileDirectory_v2()
    got = False
    for tag in TIFF_IFD0_TAGS:
        v = ex.get(tag)
        if v is not None:
            info[tag] = v
            got = True
    return info if got else None


def apply_exif_to_output(out_file: str, blob: Optional[bytes], kind: str,
                         outfmt: str) -> bool:
    """保存之后再写 EXIF 的格式：目前只有 JPEG（段注入，不重编码像素，保住 MakerNote）"""
    if not blob or not kind:
        return False
    if outfmt == "jpeg":
        seg = exif_segment(blob, kind)
        return inject_exif_into_jpeg(out_file, seg) if seg else False
    return False


def attach_exif(src) -> None:
    """导入照片时一次性把 EXIF 读好挂到 SourceImage 上，之后不必再碰源文件"""
    tags = read_exif_tags(src.path)
    src.shot_time = tags.get("shotTime", "")
    src.model = tags.get("model", "")
    blob, kind = extract_exif_blob(src.path)
    src.exif_b64 = base64.b64encode(blob).decode("ascii") if blob else ""
    src.exif_kind = kind if blob else ""


def inject_exif_into_jpeg(path: str, seg: Optional[bytes]) -> bool:
    if not seg:
        return False
    try:
        with open(path, "rb") as f:
            data = f.read()
        body_start = 2
        for marker, start, seg_len, ps, plen in _iter_jpeg_segments(data):
            if marker == 0xE1:
                if data[ps:ps + 5] == b"Exif\x00" or \
                   data[ps:ps + 28].startswith(b"http://ns.adobe.com/xap"):
                    body_start = start + 2 + seg_len
                    break
        with open(path, "wb") as f:
            f.write(data[:2] + seg + data[body_start:])
        return True
    except OSError:
        return False


def read_source(path: str):
    im = Image.open(path)
    icc = im.info.get("icc_profile")
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "RGBA", "L"):
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
    return im, icc


def probe_image(path: str) -> Tuple[int, int, str]:
    im = Image.open(path)
    fmt = (im.format or "PNG").upper()
    fmt = {"JPEG": "jpeg", "MPO": "jpeg", "PNG": "png", "TIFF": "tiff",
           "WEBP": "webp", "BMP": "png"}.get(fmt, "jpeg")
    im2 = ImageOps.exif_transpose(im)
    return im2.width, im2.height, fmt


# ==========================================================================
# 数据模型
# ==========================================================================
@dataclass
class SourceImage:
    path: str
    name: str
    natural_w: int
    natural_h: int
    format: str = "jpeg"
    sha256: str = ""
    rotation: int = 0
    bytes: int = 0
    camera: str = ""
    # EXIF：导入时由 attach_exif() 填好
    shot_time: str = ""      # DateTimeOriginal，用于「同场同光」判断
    model: str = ""          # 机身型号串
    exif_b64: str = ""       # 归一后的 EXIF，base64；内嵌进 manifest，原图不在了也能写回
    exif_kind: str = ""      # "app1" / "raw" / ""（见 extract_exif_blob）

    @property
    def w(self) -> int:
        return self.natural_h if self.rotation == 90 else self.natural_w

    @property
    def h(self) -> int:
        return self.natural_w if self.rotation == 90 else self.natural_h

    @property
    def orientation(self) -> str:
        """真实朝向（P 竖 / L 横 / S 方）。人脸朝上时就是这个朝向。"""
        return orientation_of(self.natural_w, self.natural_h)

    def area(self) -> int:
        return self.w * self.h

    def shot_dt(self) -> Optional[datetime.datetime]:
        return parse_exif_datetime(self.shot_time)


def parse_exif_datetime(s: str) -> Optional[datetime.datetime]:
    """EXIF 时间串 'YYYY:MM:DD HH:MM:SS' 转 datetime，解析不了返回 None"""
    if not s:
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M"):
        try:
            return datetime.datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def scene_time_span(plan: "CanvasPlan", srcs) -> Optional[float]:
    """画布内所有照片的拍摄时间跨度（分钟）。
    少于两张可解析的时间就返回 None（无从判断「同场」）。"""
    ts = []
    for p in plan.placements:
        d = srcs[p.src_index].shot_dt()
        if d:
            ts.append(d)
    if len(ts) < 2:
        return None
    return (max(ts) - min(ts)).total_seconds() / 60.0


def format_span(minutes: float) -> str:
    if minutes < 1:
        return "不到 1 分钟"
    if minutes < 60:
        return "%d 分钟" % round(minutes)
    hours = minutes / 60.0
    if hours < 24:
        return "%.1f 小时" % hours
    return "%.1f 天" % (hours / 24.0)


@dataclass
class Placement:
    src_index: int
    x: int
    y: int
    w: int
    h: int
    rotation: int = 0        # 0 = 保持真实朝向（人脸朝上）；90 = 排版器为提升密度转过


@dataclass
class CanvasPlan:
    index: int
    width: int
    height: int
    placements: List[Placement] = field(default_factory=list)

    def used_area(self) -> int:
        return sum(p.w * p.h for p in self.placements)

    def fill(self) -> float:
        if not self.width or not self.height:
            return 0.0
        return self.used_area() / float(self.width * self.height)


# ==========================================================================
# 排版
# ==========================================================================
def _valid(sizes, pos, gutter) -> bool:
    for i in range(len(pos)):
        xi, yi = pos[i]
        wi, hi = sizes[i][1] + 2 * gutter, sizes[i][2] + 2 * gutter
        for j in range(i + 1, len(pos)):
            xj, yj = pos[j]
            wj, hj = sizes[j][1] + 2 * gutter, sizes[j][2] + 2 * gutter
            if not (xi + wi <= xj or xj + wj <= xi or yi + hi <= yj or yj + hj <= yi):
                return False
    return True


def _shelf(sizes, max_side, gap, order, gutter):
    """货架式：从左到右填一行，满了换行。坐标系里 (x,y) 是**块**左上角（含保护带），
    内容贴在块内 +gutter 处，因此块占满 max_side 就正好用满画布，不会白丢一条保护带。"""
    pos = {}
    x = y = 0
    row_h = 0
    for i in order:
        _, w, h = sizes[i]
        ww, hh = w + 2 * gutter, h + 2 * gutter
        if x + ww > max_side:
            x = 0
            y += row_h + gap
            row_h = 0
        if y + hh > max_side:
            return None
        pos[sizes[i][0]] = (x + gutter, y + gutter)
        x += ww + gap
        row_h = max(row_h, hh)
    return [pos[s[0]] for s in sizes]


def _maxrects(sizes, max_side, gap, order, gutter):
    free = [(0, 0, max_side, max_side)]
    placed = {}

    def split(free, target, pw, ph):
        """只切**真正被占用**的那块空闲区，且切成两块互不重叠的矩形。

        原实现会对所有放得下的空闲区各切一刀（等于假设每块都被占用），
        并在右下角产生互相重叠的空闲区 —— 会让后续放置撞车、候选被 _valid 丢掉。
        """
        out = []
        for f in free:
            if f != target:
                out.append(f)
                continue
            fx, fy, fw, fh = f
            lw, lh = fw - pw, fh - ph
            # 选剩余面积更大的一侧做整条切割，避免出现重叠的空闲矩形
            if lw > lh:
                out.append((fx + pw, fy, lw, fh))
                out.append((fx, fy + ph, pw, lh))
            else:
                out.append((fx + pw, fy, lw, ph))
                out.append((fx, fy + ph, fw, lh))
        pruned = []
        for i, a in enumerate(out):
            if any(i != j and b[0] <= a[0] and b[1] <= a[1]
                   and b[0] + b[2] >= a[0] + a[2] and b[1] + b[3] >= a[1] + a[3]
                   for j, b in enumerate(out)):
                continue
            pruned.append(a)
        return pruned

    for i in order:
        idx, w, h = sizes[i]
        ww, hh = w + 2 * gutter + gap, h + 2 * gutter + gap
        best, best_score = None, None
        for f in free:
            fx, fy, fw, fh = f
            if fw >= ww and fh >= hh:
                lw, lh = fw - ww, fh - hh
                score = (min(lw, lh), max(lw, lh))
                if best_score is None or score < best_score:
                    best_score, best = score, f
        if best is None:
            return None
        fx, fy, fw, fh = best
        placed[idx] = (fx + gutter, fy + gutter)
        free = split(free, best, ww, hh)
    return [placed[s[0]] for s in sizes]


def _compact(sizes, pos, gutter):
    """紧包围盒重压：按 (y,x) 序把每张图往上、往左挤到不能再挤"""
    order = sorted(range(len(pos)), key=lambda i: (pos[i][1], pos[i][0]))
    new = [None] * len(pos)
    boxes = []
    for i in order:
        _, w, h = sizes[i]
        ww, hh = w + 2 * gutter, h + 2 * gutter
        bx, by = pos[i]

        def clash(x, y):
            box = (x - gutter, y - gutter, ww, hh)
            for b in boxes:
                if not (box[0] + box[2] <= b[0] or b[0] + b[2] <= box[0] or
                        box[1] + box[3] <= b[1] or b[1] + b[3] <= box[1]):
                    return True
            return False

        while by - 1 - gutter >= 0 and not clash(bx, by - 1):
            by -= 1
        while bx - 1 - gutter >= 0 and not clash(bx - 1, by):
            bx -= 1
        new[i] = (bx, by)
        boxes.append((bx - gutter, by - gutter, ww, hh))
    return new


def _apply_rot(sub, rots):
    """把 sizes 按每张的 rotation 换算成实际占位尺寸（转 90° 就是宽高互换）。"""
    out = []
    for i, s in enumerate(sub):
        w, h = s[1], s[2]
        if rots and rots[i] == 90:
            w, h = h, w
        out.append((s[0], w, h))
    return out


def _bands(sizes, max_side, gap, order, gutter, allow_rotate=False):
    """朝向感知的**列带分解** —— 「竖屏竖着排、横屏横着排」的几何实现。

    把画布切成若干竖直列：列宽 = 列内最宽的块 (w+2g)，列内竖向堆叠。
    竖图窄而高，天生适合填满一列；横图会把整列撑宽，所以横图偏好「横向几行」。
    两者混排时宽度恰好互补（A7M4：4696 + 7032 = 11728 ≤ 12000 → 1 竖 + 2 横 = 3 张）。

    allow_rotate=True 时才允许把某几张转 90° 塞进窄列（人脸会侧躺，纯属可选增益）。
    返回 (pos_list, rot_list)，与 sizes 同序；放不下返回 None。
    """
    items = [sizes[j] for j in order]
    n = len(items)
    # 每列记录：items=[(k,w,h)]、bottom=已排内容的底边 y、w=列宽(含两侧保护带)
    cols: List[Dict[str, Any]] = []
    used_w = float(gutter)
    rots = [0] * n

    def col_span(c):
        return c["bottom"] + gutter          # 该列连同底边保护带占到的画布高度

    for k in range(n):
        _, w0, h0 = items[k]
        opts = [(w0, h0, 0)]
        if allow_rotate and w0 != h0:
            opts.append((h0, w0, 90))
        peak = max([col_span(c) for c in cols], default=0.0)
        best = None
        for (w, h, rot) in opts:
            bw = w + 2 * gutter
            if bw > max_side:
                continue
            # ① 塞进已有列（列宽不够就把整列撑宽；新内容排在列底，留 2g 让保护带不相交）
            for c in cols:
                y = c["bottom"] + 2 * gutter + gap
                if y + h + gutter > max_side:
                    continue
                n_used = used_w + max(0, bw - c["w"])
                if n_used > max_side:
                    continue
                n_span = max(y + h + gutter, peak)
                score = (n_used * n_span, n_span, n_used)
                if best is None or score < best[0]:
                    best = (score, c, w, h, rot, max(0, bw - c["w"]), y + h)
            # ② 新开一列
            if gutter + h + gutter <= max_side:
                extra = (gap if cols else 0) + bw
                if used_w + extra <= max_side:
                    n_span = max(gutter + h + gutter, peak)
                    score = ((used_w + extra) * n_span, n_span, used_w + extra)
                    if best is None or score < best[0]:
                        best = (score, None, w, h, rot, extra, 0)
        if best is None:
            return None
        _, c, w, h, rot, dw, dh = best
        if c is None:
            c = {"items": [], "bottom": 0.0, "w": 0.0}
            cols.append(c)
            c["w"] = w + 2 * gutter
            c["bottom"] = gutter + h
        else:
            if w + 2 * gutter > c["w"]:
                c["w"] = w + 2 * gutter
            c["bottom"] = dh
        used_w += dw
        c["items"].append((k, w, h))
        rots[k] = rot

    pos: List[Any] = [None] * len(sizes)
    rot_out = [0] * len(sizes)
    x = float(gutter)
    for c in cols:
        y = float(gutter)
        for (k, w, h) in c["items"]:
            cx = x + (c["w"] - (w + 2 * gutter)) // 2 + gutter   # 列内水平居中
            pos[order[k]] = (int(cx), int(y + gutter))
            rot_out[order[k]] = rots[k]
            y += h + 2 * gutter + gap
        x += c["w"] + gap
    return pos, rot_out


def _try_pack(group, sizes, max_side, gap, gutter, rng, allow_rotate=False,
              effort=2) -> Optional[CanvasPlan]:
    """把一组照片塞进**一张**画布。多排序 × 多算法，取面积最小的方案。

    effort=2 全量（8 排序键 + 24 随机起点）；1 只跑确定性排序键；0 更少。
    装箱可行性判断用低 effort、最终定稿用 effort=2，方向安全（解空间是超集）。
    """
    sub = [sizes[i] for i in group]
    n = len(sub)
    keys = [
        lambda s: (-s[2], -s[1]),
        lambda s: (-s[1], -s[2]),
        lambda s: (-(s[1] * s[2]), -max(s[1], s[2])),
        lambda s: (-max(s[1], s[2]), -min(s[1], s[2])),
        lambda s: (-(s[1] + s[2]), -s[1] * s[2]),
        lambda s: (-min(s[1], s[2]), -max(s[1], s[2])),
        # 朝向优先：竖图先排（形成窄列）/ 横图先排（形成宽列）
        lambda s: (0 if s[2] > s[1] else 1, -s[2], -s[1]),
        lambda s: (0 if s[1] > s[2] else 1, -s[1], -s[2]),
    ]
    if effort <= 0:
        keys = keys[:4]
    orders = [sorted(range(n), key=lambda j, k=k: k(sub[j])) for k in keys]
    if effort >= 2 and n > 2:
        for _ in range(24):
            o = list(range(n))
            rng.shuffle(o)
            orders.append(o)

    best = None
    for order in orders:
        cands = []
        for algo in (_shelf, _maxrects):
            p = algo(sub, max_side, gap, order, gutter)
            if p is not None:
                cands.append((p, [0] * n))
        b = _bands(sub, max_side, gap, order, gutter, allow_rotate=allow_rotate)
        if b is not None:
            cands.append(b)
        for p, rots in cands:
            osub = _apply_rot(sub, rots)
            if not _valid(osub, p, gutter):
                continue
            cpos = _compact(osub, p, gutter)
            if not _valid(osub, cpos, gutter):
                cpos = p
            w = max(cpos[i][0] + osub[i][1] + gutter for i in range(n))
            h = max(cpos[i][1] + osub[i][2] + gutter for i in range(n))
            if w > max_side or h > max_side:
                continue
            area = w * h
            if best is None or area < best[0]:
                best = (area, w, h, list(cpos), list(rots))
    if best is None:
        return None
    _, w, h, pos, rots = best
    osub = _apply_rot(sub, rots)
    pls = [Placement(group[j], pos[j][0], pos[j][1],
                     osub[j][1], osub[j][2], rots[j]) for j in range(n)]
    return CanvasPlan(0, w, h, pls)


def _total_area(plans) -> int:
    return sum(p.width * p.height for p in plans)


def _area_item_cap(sizes, max_side, gutter) -> int:
    """单画布张数的**面积上界**（严格成立的剪枝依据）：块(含保护带)两两不重叠又在画布内，
    所以前 m 小的块面积之和一旦超过画布面积，就绝不可能装下 m 张。
    用它挡掉「这张画布已经塞满、不可能再进一张」的无效装箱搜索。"""
    cap_area = max_side * max_side
    tot = cnt = 0
    for b in sorted(_block_area(w, h, gutter) for _, w, h in sizes):
        if tot + b > cap_area:
            break
        tot += b
        cnt += 1
    return max(1, cnt)


def _canvas_lower_bound(sizes, max_side, gutter) -> int:
    """画布数下界：面积下界 + 单画布容量上界。达到即最优，可提前收工。"""
    n = len(sizes)
    if not n:
        return 0
    usable_area = max(1, (max_side - 2 * gutter) ** 2)
    lb_area = -(-sum(w * h for _, w, h in sizes) // usable_area)
    cap = _area_item_cap(sizes, max_side, gutter)
    return max(1, lb_area, -(-n // cap))


def _pack_starts(sizes):
    """多起点：几套确定性排序，覆盖「面积优先 / 长边优先 / 朝向优先」几种分组倾向。"""
    n = len(sizes)
    idx = list(range(n))
    defs = [
        lambda i: -sizes[i][1] * sizes[i][2],
        lambda i: (-max(sizes[i][1], sizes[i][2]), -min(sizes[i][1], sizes[i][2])),
        lambda i: (0 if sizes[i][2] > sizes[i][1] else 1, -sizes[i][2], -sizes[i][1]),
        lambda i: (0 if sizes[i][1] > sizes[i][2] else 1, -sizes[i][1], -sizes[i][2]),
        lambda i: -min(sizes[i][1], sizes[i][2]),
    ]
    starts = []
    for k in defs:
        o = sorted(idx, key=k)
        if o not in starts:
            starts.append(o)
    return starts


def _block_area(w, h, gutter) -> int:
    return (w + 2 * gutter) * (h + 2 * gutter)


def _group_area(group, sizes, gutter) -> int:
    return sum(_block_area(sizes[i][1], sizes[i][2], gutter) for i in group)


_GREEDY_SCAN_CAP = 16     # 每张照片最多试投这么多个「最空的」画布


def _greedy_groups(order, sizes, max_side, gap, gutter, rng, allow_rotate, over_budget,
                   item_cap):
    """首次适应装箱：能塞进已有画布就塞，塞不下才开新画布。

    两层剪枝，保证 1000 张规模也能在预算内跑完：
      ① 「块面积之和 ≤ 画布面积」——块(含保护带)两两不重叠且都在画布内，面积和必然
         ≤ 画布面积，明显超了就 O(1) 跳过；
      ② 只试投**最空的 16 个**画布。照片按尺寸降序到达、各画布填充度基本同步，
         最空的那些就是最有可能装得下的；不设这一层的话每张照片都要扫全部画布
         （180 张 × 60 画布 ≈ 1 万次装箱搜索）。
    """
    cap_area = max_side * max_side
    groups: List[List[int]] = []
    areas: List[int] = []
    for idx in order:
        block = _block_area(sizes[idx][1], sizes[idx][2], gutter)
        effort = 1 if over_budget() else 2
        cand_order = sorted(range(len(groups)), key=lambda i: (len(groups[i]), i))
        for gi in cand_order[:_GREEDY_SCAN_CAP]:
            if len(groups[gi]) >= item_cap or areas[gi] + block > cap_area:
                continue
            if _try_pack(groups[gi] + [idx], sizes, max_side, gap, gutter, rng,
                         allow_rotate, effort=effort) is not None:
                groups[gi].append(idx)
                areas[gi] += block
                break
        else:
            groups.append([idx])
            areas.append(block)
    return groups


def _merge_plans(plans, sizes, max_side, gap, gutter, allow_rotate, item_cap):
    """画布合并：任意两张画布若能并成一张，就并。"""
    cap_area = max_side * max_side
    merged, guard = True, 0
    while merged and guard < 50:
        merged, guard = False, guard + 1
        areas = [_group_area([p.src_index for p in pl.placements], sizes, gutter)
                 for pl in plans]
        for i in range(len(plans)):
            for j in range(i + 1, len(plans)):
                if len(plans[i].placements) + len(plans[j].placements) > item_cap:
                    continue
                if areas[i] + areas[j] > cap_area:
                    continue
                trial = [p.src_index for p in plans[i].placements] + \
                        [p.src_index for p in plans[j].placements]
                cand = _try_pack(trial, sizes, max_side, gap, gutter,
                                 random.Random((i * 31 + j) & 0xFFFF),
                                 allow_rotate, effort=2)
                if cand is not None:
                    plans[i] = cand
                    plans.pop(j)
                    merged = True
                    break
            if merged:
                break
    return plans


def _refill_plans(plans, sizes, max_side, gap, gutter, allow_rotate, item_cap):
    """搬空重填：把最空的那张画布整张拆开，逐张塞进别的画布；全塞得下就撤掉它。"""
    cap_area = max_side * max_side
    changed, guard = True, 0
    while changed and guard < 30:
        changed, guard = False, guard + 1
        order = sorted(range(len(plans)), key=lambda i: len(plans[i].placements))
        for vi in order:
            if len(plans) <= 1:
                break
            victim = plans[vi]
            items = [p.src_index for p in victim.placements]
            targets = [p for p in plans if p is not victim]
            targets.sort(key=lambda p: len(p.placements))
            snaps, ok = [], True
            for it in items:
                block = _block_area(sizes[it][1], sizes[it][2], gutter)
                hit = None
                for pj in targets:
                    if len(pj.placements) >= item_cap:
                        continue
                    if _group_area([q.src_index for q in pj.placements], sizes,
                                   gutter) + block > cap_area:
                        continue
                    cand = _try_pack([q.src_index for q in pj.placements] + [it],
                                     sizes, max_side, gap, gutter,
                                     random.Random(4242), allow_rotate, effort=2)
                    if cand is not None:
                        hit = (pj, cand)
                        break
                if hit is None:
                    ok = False
                    break
                pj, cand = hit
                snaps.append((pj, pj.placements, pj.width, pj.height))
                pj.placements, pj.width, pj.height = (cand.placements, cand.width,
                                                      cand.height)
            if ok:
                plans.remove(victim)
                changed = True
                break
            for (pj, pls, w, h) in reversed(snaps):      # 回滚
                pj.placements, pj.width, pj.height = pls, w, h
    return plans


def pack_canvases(srcs, max_side, gap=0, gutter=PIXCAKE_RECOMMENDED_GUTTER,
                  allow_rotate=False, time_budget_ms=6000,
                  progress: Optional[Callable] = None) -> List[CanvasPlan]:
    """把一批照片排到尽量少的画布上（每张画布单边 ≤ max_side）。

    默认 allow_rotate=False：**所有照片保持真实朝向**（人脸朝上），靠「竖图竖排 +
    横图横排 + 横竖混排」拿密度。打开 allow_rotate 后由排版器按需旋转个别照片，
    是纯增益选项，代价是那几张的人脸会侧躺。
    """
    if not srcs:
        return []
    sizes = [(i, s.natural_w, s.natural_h) for i, s in enumerate(srcs)]
    for s in srcs:
        lo = min(s.natural_w, s.natural_h) + 2 * gutter + gap
        hi = max(s.natural_w, s.natural_h) + 2 * gutter + gap
        if lo > max_side or (hi > max_side and not allow_rotate):
            hint = "或勾选「允许旋转 90°」" if hi > max_side else ""
            raise ValueError(
                "「%s」%d×%d 加保护带后单边要 %dpx，超过画布上限 %dpx。"
                "请调高「画布单边上限」%s。" % (s.name, s.natural_w, s.natural_h,
                                              hi, max_side, hint))

    t0 = time.time()

    def over_budget():
        return (time.time() - t0) * 1000 > time_budget_ms

    lb = _canvas_lower_bound(sizes, max_side, gutter)
    # 单画布张数的面积上界：装满了就 O(1) 跳过，不再白跑一次装箱搜索
    item_cap = _area_item_cap(sizes, max_side, gutter)
    starts = _pack_starts(sizes)
    best: Optional[List[CanvasPlan]] = None
    for si, order in enumerate(starts):
        rng = random.Random(20240101 + si * 7919)
        groups = _greedy_groups(order, sizes, max_side, gap, gutter,
                                rng, allow_rotate, over_budget, item_cap)
        plans: List[CanvasPlan] = []
        for gi, g in enumerate(groups):
            plan = _try_pack(g, sizes, max_side, gap, gutter,
                             random.Random(20240101 + gi * 104729),
                             allow_rotate, effort=2)
            if plan is None:
                raise RuntimeError("内部错误：分组可行但重排失败")
            plan.index = gi
            plans.append(plan)
            if progress:
                progress(gi + 1, len(groups), "排版画布 %d/%d" % (gi + 1, len(groups)))
        plans = _merge_plans(plans, sizes, max_side, gap, gutter, allow_rotate, item_cap)
        plans = _refill_plans(plans, sizes, max_side, gap, gutter, allow_rotate, item_cap)
        if best is None or (len(plans), _total_area(plans)) < (len(best), _total_area(best)):
            best = plans
        if len(best) <= lb:
            break
        if over_budget() and si >= 1:
            break

    for k, p in enumerate(best):
        p.index = k
        p.placements.sort(key=lambda x: (x.y, x.x))
        if min(p.width, p.height) < PIXCAKE_MIN_SIDE:
            raise ValueError(
                "画布 %d×%d 小于像素蛋糕导入下限 %dpx（长宽需 %d~%d）。"
                "一般是混进了极小的照片，请移除后重排。"
                % (p.width, p.height, PIXCAKE_MIN_SIDE,
                   PIXCAKE_MIN_SIDE, max_side))
    return best


def verify_plan(plan, srcs, max_side, gutter) -> List[str]:
    """校验一张画布的排布是否合法。返回问题列表，空列表 = 通过。

    检查项：不超单边 / 内容与保护带都在画布内 / 相邻两张的保护带互不侵占 /
    朝向一致（未旋转时占位尺寸必须等于原图尺寸 —— 保证人脸是正的）。
    """
    errs = []
    if plan.width > max_side or plan.height > max_side:
        errs.append("画布 %d×%d 超过单边上限 %d" % (plan.width, plan.height, max_side))
    if min(plan.width, plan.height) < PIXCAKE_MIN_SIDE:
        errs.append("画布 %d×%d 低于导入下限 %d"
                    % (plan.width, plan.height, PIXCAKE_MIN_SIDE))
    n = len(plan.placements)
    for i in range(n):
        a = plan.placements[i]
        s = srcs[a.src_index]
        if a.x < gutter or a.y < gutter:
            errs.append("第 %d 张越过保护带边界（%d,%d）" % (i + 1, a.x, a.y))
        if a.x + a.w + gutter > plan.width or a.y + a.h + gutter > plan.height:
            errs.append("第 %d 张连带保护带超出画布" % (i + 1))
        if a.rotation == 0:
            if (a.w, a.h) != (s.natural_w, s.natural_h):
                errs.append("第 %d 张未旋转，占位 %d×%d ≠ 原图 %d×%d"
                            % (i + 1, a.w, a.h, s.natural_w, s.natural_h))
        elif a.rotation == 90:
            if (a.w, a.h) != (s.natural_h, s.natural_w):
                errs.append("第 %d 张旋转 90°，占位 %d×%d ≠ 原图转置 %d×%d"
                            % (i + 1, a.w, a.h, s.natural_h, s.natural_w))
        else:
            errs.append("第 %d 张 rotation 非法：%r" % (i + 1, a.rotation))
        for j in range(i + 1, n):
            b = plan.placements[j]
            if not (a.x + a.w + 2 * gutter <= b.x or b.x + b.w + 2 * gutter <= a.x or
                    a.y + a.h + 2 * gutter <= b.y or b.y + b.h + 2 * gutter <= a.y):
                errs.append("第 %d 张与第 %d 张的保护带相交" % (i + 1, j + 1))
    return errs


def _strip_plan(items, gutter, gap) -> CanvasPlan:
    """把若干 (src_index, main, cross, horiz, rot) 排成一行（row）或一列（col）。

    每张图按**原始像素**放置，交叉轴居中、多出来的部分留给背景色（留白对齐），
    绝不缩放——这样切回来才是原像素。同尺寸时与「等比缩放」版效果完全一致。
    """
    horiz = items[0][3]
    band = max(item[2] for item in items)              # 本画布交叉轴尺寸
    pls, m = [], gutter
    for idx, main, cross, _, rot in items:
        off = gutter + (band - cross) // 2             # 交叉轴居中
        if horiz:
            pls.append(Placement(idx, m, off, main, cross, rot))
        else:
            pls.append(Placement(idx, off, m, cross, main, rot))
        m += main + 2 * gutter + gap                   # 边到边留 2*gutter + gap
    last = pls[-1]
    if horiz:
        return CanvasPlan(0, last.x + last.w + gutter, band + 2 * gutter, pls)
    return CanvasPlan(0, band + 2 * gutter, last.y + last.h + gutter, pls)


def pack_strip(srcs, max_side, axis="row", gap=0,
               gutter=PIXCAKE_RECOMMENDED_GUTTER,
               progress: Optional[Callable] = None) -> List[CanvasPlan]:
    """row / col 版式：按原始像素一字排开，保证 1:1 无损切回。

    与 pack_canvases 的唯一差别是**不重采样**：
      - 每张图保持 natural 像素尺寸，交叉轴居中，画布多余部分填背景色。
        同尺寸时结果与「等比缩放」版完全一致；混排时不缩放，切回就是原像素。
      - 沿排列方向贪心装满，一张画布放不下就自动开新画布（每个仍是一行 / 一列）。
    **不做 90° 旋转**：row/col 里「转一张」等于让那张人脸侧躺，与修人像冲突；
    想靠旋转提密度请用 grid 版式。
    """
    if not srcs:
        return []
    horiz = (axis == "row")
    # main = 排列方向的像素长度；cross = 交叉轴像素长度
    dims = [(s.natural_w, s.natural_h) if horiz else (s.natural_h, s.natural_w)
            for s in srcs]
    band = max(c[1] for c in dims)
    if band + 2 * gutter > max_side:
        raise ValueError(
            "有照片交叉轴 %dpx，加保护带后超过画布单边上限 %dpx，"
            "请调高「画布单边上限」。" % (band, max_side))

    plans: List[CanvasPlan] = []
    cur: List[Tuple[int, int, int, bool, int]] = []   # (src_index, main, cross, horiz, rot)
    cursor = gutter
    for k, src in enumerate(srcs):
        main, cross = dims[k]
        if cur and cursor + main + gutter > max_side:
            plans.append(_strip_plan(cur, gutter, gap))
            cur, cursor = [], gutter
        if cursor + main + gutter > max_side:
            raise ValueError(
                "「%s」按 %s 版式单边需要 %dpx，超过画布上限 %dpx，"
                "请调高「画布单边上限」或改用 grid 版式。"
                % (src.name, axis, cursor + main + gutter, max_side))
        cur.append((k, main, cross, horiz, 0))
        cursor += main + 2 * gutter + gap
        if progress:
            progress(k + 1, len(srcs), src.name)
    if cur:
        plans.append(_strip_plan(cur, gutter, gap))
    for i, p in enumerate(plans):
        p.index = i
        if min(p.width, p.height) < PIXCAKE_MIN_SIDE:
            raise ValueError(
                "画布 %d×%d 小于像素蛋糕导入下限 %dpx（长宽需 %d~%d）。"
                "一般是混进了极小的照片，请移除后重排。"
                % (p.width, p.height, PIXCAKE_MIN_SIDE,
                   PIXCAKE_MIN_SIDE, max_side))
    return plans


# ==========================================================================
# 合成
# ==========================================================================
def _mirror_strips(im: Image.Image, gutter: int):
    w, h = im.size
    g = gutter
    if g <= 0:
        return []
    gl, gt = min(g, w), min(g, h)
    out = [
        (im.crop((0, 0, gl, h)).transpose(Image.FLIP_LEFT_RIGHT), (-gl, 0)),
        (im.crop((w - gl, 0, w, h)).transpose(Image.FLIP_LEFT_RIGHT), (w, 0)),
        (im.crop((0, 0, w, gt)).transpose(Image.FLIP_TOP_BOTTOM), (0, -gt)),
        (im.crop((0, h - gt, w, h)).transpose(Image.FLIP_TOP_BOTTOM), (0, h)),
        (im.crop((0, 0, gl, gt)).transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.FLIP_TOP_BOTTOM), (-gl, -gt)),
        (im.crop((w - gl, 0, w, gt)).transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.FLIP_TOP_BOTTOM), (w, -gt)),
        (im.crop((0, h - gt, gl, h)).transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.FLIP_TOP_BOTTOM), (-gl, h)),
        (im.crop((w - gl, h - gt, w, h)).transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.FLIP_TOP_BOTTOM), (w, h)),
    ]
    return out


def compose_canvas(plan: CanvasPlan, srcs, out_path: str,
                   gutter=PIXCAKE_RECOMMENDED_GUTTER,
                   background=(128, 128, 128),
                   progress: Optional[Callable] = None) -> None:
    canvas = Image.new("RGB", (plan.width, plan.height), background)
    icc = None
    total = len(plan.placements)
    for k, pl in enumerate(plan.placements):
        src = srcs[pl.src_index]
        im, src_icc = read_source(src.path)
        if icc is None and src_icc:
            icc = src_icc
        if pl.rotation == 90:
            im = im.rotate(-90, expand=True)
        if im.size != (pl.w, pl.h):
            # 绝不能悄悄重采样：一旦缩放，切回就不是原像素，1:1 承诺失效
            raise ValueError(
                "「%s」实际 %d×%d 与排版占位 %d×%d 不一致，合成会破坏 1:1 切回。"
                "请移除后重新添加该照片。"
                % (src.name, im.width, im.height, pl.w, pl.h))
        if im.mode != "RGB":
            im = im.convert("RGB")

        for strip, (dx, dy) in _mirror_strips(im, gutter):
            px, py = pl.x + dx, pl.y + dy
            sw, sh = strip.size
            if sw <= 0 or sh <= 0:
                continue
            cx0, cy0 = max(0, px), max(0, py)
            cx1, cy1 = min(plan.width, px + sw), min(plan.height, py + sh)
            if cx1 <= cx0 or cy1 <= cy0:
                continue
            canvas.paste(strip.crop((cx0 - px, cy0 - py, cx1 - px, cy1 - py)), (cx0, cy0))

        canvas.paste(im, (pl.x, pl.y))
        del im
        if progress:
            progress(k + 1, total, src.name)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    tmp = out_path + ".part"
    kw = {"icc_profile": icc} if icc else {}
    ext = os.path.splitext(out_path)[1].lower()
    if ext in (".tif", ".tiff"):
        canvas.save(tmp, "TIFF", compression="tiff_lzw", **kw)
    elif ext in (".jpg", ".jpeg"):
        canvas.save(tmp, "JPEG", quality=98, subsampling=0, **kw)
    else:
        canvas.save(tmp, "PNG", compress_level=6, **kw)
    os.replace(tmp, out_path)


# ==========================================================================
# manifest
# ==========================================================================
def manifest_path_for(canvas_file: str) -> str:
    return os.path.splitext(canvas_file)[0] + ".manifest.json"


def build_manifest(plan, srcs, canvas_file, gutter, gap, batch_id,
                   rotate_used, max_side, camera="") -> Dict[str, Any]:
    return {
        "app": APP_SIGNATURE,
        "version": MANIFEST_VERSION,
        "created": now_iso(),
        "batch": batch_id,
        "canvas": {
            "file": os.path.basename(canvas_file),
            "width": plan.width, "height": plan.height,
            "gutter": gutter, "gap": gap, "maxSide": max_side,
        },
        "settings": {
            "rotateUsed": rotate_used,
            "pixcakeExportFormat": PIXCAKE_EXPORT_FORMAT,
            "camera": camera,
        },
        "items": [{
            "index": k,
            "source": os.path.abspath(srcs[p.src_index].path),
            "name": srcs[p.src_index].name,
            "format": srcs[p.src_index].format,
            "natural": {"width": srcs[p.src_index].natural_w,
                        "height": srcs[p.src_index].natural_h},
            "rotation": p.rotation,
            "crop": {"left": p.x, "top": p.y, "width": p.w, "height": p.h},
            "sha256": srcs[p.src_index].sha256,
            # v5：EXIF 内嵌进 manifest，原图归档 / 改名 / 移走后照样写得回
            "shotTime": srcs[p.src_index].shot_time,
            "model": srcs[p.src_index].model,
            "exifKind": srcs[p.src_index].exif_kind,
            "exifB64": srcs[p.src_index].exif_b64,
        } for k, p in enumerate(plan.placements)],
    }


def write_manifest(man, canvas_file) -> str:
    p = manifest_path_for(canvas_file)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
    return p


# ==========================================================================
# 切分
# ==========================================================================
def split_canvas(returned_file, manifest, out_dir, fmt="jpeg", quality=100,
                 keep_exif=True, progress: Optional[Callable] = None) -> Dict[str, Any]:
    items = manifest.get("items") or []
    if not items:
        raise ValueError("manifest 里没有任何图片记录")
    os.makedirs(out_dir, exist_ok=True)

    exp_w = manifest["canvas"]["width"]
    exp_h = manifest["canvas"]["height"]
    im = ImageOps.exif_transpose(Image.open(returned_file))
    act_w, act_h = im.size

    rep = {
        "returnedFile": returned_file,
        "expected": {"width": exp_w, "height": exp_h},
        "actual": {"width": act_w, "height": act_h},
        "dimsMatch": act_w == exp_w and act_h == exp_h,
        "outputs": [], "warnings": [],
    }
    sx = sy = 1.0
    if not rep["dimsMatch"]:
        rx, ry = act_w / float(exp_w), act_h / float(exp_h)
        if abs(rx - ry) > 0.002:
            raise ValueError(
                f"返回文件 {act_w}×{act_h} 与画布 {exp_w}×{exp_h} 宽高比不一致，"
                f"无法可靠切分（用了裁剪或排版印刷？）")
        sx = sy = rx
        rep["warnings"].append(
            f"尺寸 {act_w}×{act_h} ≠ 画布 {exp_w}×{exp_h}，按等比 {rx*100:.2f}% 映射"
            f"（有一次重采样）")

    icc = im.info.get("icc_profile")
    total = len(items)
    for k, it in enumerate(items):
        c = it["crop"]
        l = max(0, min(int(round(c["left"] * sx)), act_w - 1))
        t = max(0, min(int(round(c["top"] * sy)), act_h - 1))
        w = max(1, min(int(round(c["width"] * sx)), act_w - l))
        h = max(1, min(int(round(c["height"] * sy)), act_h - t))

        crop = im.crop((l, t, l + w, t + h))
        rot = it.get("rotation", 0)
        if rot == 90:
            crop = crop.rotate(90, expand=True)

        stem = os.path.splitext(it.get("name") or f"image-{k+1}")[0]
        stem = "".join(ch for ch in stem if ch not in '\\/:*?"<>|')
        outfmt = it.get("format", "jpeg") if fmt == "original" else fmt
        ext = {"jpeg": ".jpg", "tiff": ".tif", "png": ".png", "webp": ".webp"}.get(outfmt, ".jpg")
        out_file = os.path.join(out_dir, stem + ext)
        n = 1
        while os.path.exists(out_file):
            out_file = os.path.join(out_dir, f"{stem}_{n}{ext}")
            n += 1

        # 取 EXIF：manifest 内嵌优先，回落到源文件路径
        blob, kind = exif_blob_for_item(it) if keep_exif else (None, "")

        kw = {"icc_profile": icc} if icc else {}
        if outfmt == "tiff":
            info = tiff_ifd0(blob, kind)
            if info is not None:
                kw["tiffinfo"] = info
        else:
            kw.update(save_exif_kwargs(blob, kind, outfmt))
        try:
            if outfmt == "jpeg":
                crop.convert("RGB").save(out_file, "JPEG", quality=quality, subsampling=0, **kw)
            elif outfmt == "tiff":
                crop.convert("RGB").save(out_file, "TIFF", compression="tiff_lzw", **kw)
            elif outfmt == "png":
                crop.save(out_file, "PNG", compress_level=6, **kw)
            else:
                crop.save(out_file, "WEBP", quality=95, **kw)
        except Exception as e:
            rep["outputs"].append({
                "index": k, "name": os.path.basename(out_file), "file": None,
                "error": str(e), "natural": it.get("natural"), "rotation": rot,
            })
            continue

        exif_ok = exif_none = False
        if keep_exif:
            if not blob or not kind:
                exif_none = True
            elif outfmt == "jpeg":
                exif_ok = apply_exif_to_output(out_file, blob, kind, outfmt)
            elif outfmt == "tiff":
                # Pillow 的 TIFF 编码器写不了 ExifIFD 子目录，只能带 IFD0（机型/时间/方向）
                exif_ok = bool(kw.get("tiffinfo"))
                rep["warnings"].append(
                    "第 %d 张（%s）输出为 TIFF：Pillow 无法写入 ExifIFD 子目录，"
                    "光圈 / 快门 / ISO / 镜头 等子目录信息未能带出；"
                    "需要完整 EXIF 请选 JPEG 或 PNG 输出。"
                    % (k + 1, it.get("name") or "?"))
            else:
                # png / webp 已在保存阶段随 exif= 写入（完整 EXIF）
                exif_ok = bool(kw.get("exif"))
        if exif_none:
            rep["warnings"].append(
                "第 %d 张（%s）没有可写回的 EXIF，输出将缺少拍摄信息"
                % (k + 1, it.get("name") or "?"))

        rep["outputs"].append({
            "index": k,
            "name": os.path.basename(out_file),
            "file": out_file,
            "size": f"{(h if rot == 90 else w)}×{(w if rot == 90 else h)}",
            "natural": it.get("natural"),
            "rotation": rot,
            # 尺寸一致即逐像素无损；旋转 90° 用 transpose 还原，同样不重采样
            "lossless": rep["dimsMatch"],
            "exifCopied": exif_ok,
            "exifNothingToCopy": exif_none,
        })
        del crop
        if progress:
            progress(k + 1, total, os.path.basename(out_file))
    return rep


# ==========================================================================
# 账本
# ==========================================================================
PRICE_PER_SHOT_DEFAULT = 299.0 / 800.0


def ledger_add(ledger_path, batch) -> None:
    data = {"batches": []}
    if os.path.exists(ledger_path):
        try:
            with open(ledger_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"batches": []}
    data.setdefault("batches", []).append(batch)
    os.makedirs(os.path.dirname(os.path.abspath(ledger_path)) or ".", exist_ok=True)
    # 先写 .part 再替换：中途断电也不会把账本写成半截 JSON
    tmp = ledger_path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ledger_path)


def ledger_summary(ledger_path, price_per_shot=PRICE_PER_SHOT_DEFAULT) -> dict:
    empty = {"count": 0, "savedShots": 0, "savedMoney": 0.0, "batches": []}
    if not os.path.exists(ledger_path):
        return empty
    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return empty
    batches = [b for b in data.get("batches", []) if not b.get("removed")]
    shots = sum(int(b.get("savedShots", 0) or 0) for b in batches)
    return {
        "count": len(batches),
        "savedShots": shots,
        "savedMoney": round(shots * price_per_shot, 2),
        "batches": batches,
    }


# ==========================================================================
# 自检
# ==========================================================================
def self_test(verbose=True) -> int:
    import tempfile
    import numpy as np

    tmp = tempfile.mkdtemp(prefix="pct2_")
    rng = random.Random(7)
    log = print if verbose else (lambda *a: None)
    ok = True

    paths = []
    for i, (w, h) in enumerate([(2000, 1333), (1200, 1800), (1600, 1600)]):
        im = Image.new("RGB", (w, h), (30, 30, 30))
        s = max(8, w // 16)
        im.paste((255, 0, 0), (0, 0, s, s))
        im.paste((0, 0, 255), (w - s, h - s, w, h))
        px = im.load()
        for yy in range(h // 3, min(h, h // 3 + 64)):
            for xx in range(w // 3, min(w, w // 3 + 64)):
                v = 255 if rng.random() > 0.5 else 0
                px[xx, yy] = (v, v, v)
        p = os.path.join(tmp, f"s{i+1}.jpg")
        im.save(p, quality=98, subsampling=0)
        paths.append(p)

    srcs = []
    for p in paths:
        w, h, fmt = probe_image(p)
        s = SourceImage(p, os.path.basename(p), w, h, fmt, sha256_file(p))
        attach_exif(s)
        srcs.append(s)

    plans = pack_canvases(srcs, PIXCAKE_SAFE_LIMIT)
    log(f"[1] {len(srcs)} 张 -> {len(plans)} 画布")
    for pl in plans:
        log(f"    {pl.width}×{pl.height}  {len(pl.placements)} 张  填充 {pl.fill()*100:.1f}%")
        for e in verify_plan(pl, srcs, PIXCAKE_SAFE_LIMIT, 12):
            log("    !! " + e)
            ok = False

    for pl in plans:
        out = os.path.join(tmp, f"T_c{pl.index+1}.tif")
        compose_canvas(pl, srcs, out)
        man = build_manifest(pl, srcs, out, 12, 0, "T", False, PIXCAKE_SAFE_LIMIT)
        write_manifest(man, out)
        rep = split_canvas(out, man, os.path.join(tmp, f"b{pl.index}"), fmt="png")
        for it, o in zip(man["items"], rep["outputs"]):
            a = Image.open(o["file"]).convert("RGB")
            b = Image.open(it["source"]).convert("RGB")
            d = int(np.abs(np.asarray(a, np.int16) - np.asarray(b, np.int16)).max())
            if d != 0:
                log(f"    !! {o['name']} 偏差 {d}")
                ok = False

    cap = capacity_report(7008, 4672)
    log(f"[2] A7M4 33MP: 横 {cap['landscape']['total']} 竖 {cap['portrait']['total']} 混 {cap['mixed']}")
    log(f"[3] 机型识别 11648x8736 -> {detect_camera(11648, 8736)}")
    # EXIF 机型识别：重点防「EOS R」把「EOS R5」抢走（泛称出现在型号前面）
    for mk, md, want in (("SONY", "ILCE-7M4", "Sony A7 IV (33MP)"),
                         ("CANON", "Canon EOS R5", "Canon R5 (45MP)"),
                         ("Canon", "Canon EOS R", "Canon R (30MP)"),
                         ("Canon", "EOS R5m2", "Canon R5 II (45MP)"),
                         ("NIKON CORPORATION", "NIKON Z 8", "Nikon Z8 (46MP)"),
                         ("", "", "")):
        got = detect_camera_exif(mk, md)
        if got != want:
            log("    !! 机型识别 %r/%r -> %r，期望 %r" % (mk, md, got, want))
            ok = False
    log("[3b] EXIF 机型识别 6 例校验完成")

    # [4][5] EXIF 全链路：JPEG / TIFF / PNG 三种源图 -> 三种输出格式，都要能完整读回拍摄信息
    def _make_exif_src(name, ext, pilfmt, model, when):
        im = Image.new("RGB", (1600, 1100), (40, 60, 80))
        ex = Image.Exif()
        ex[TAG_MAKE] = "SONY"
        ex[TAG_MODEL] = model
        ex[TAG_DATETIME] = when
        ex[TAG_ORIENTATION] = 1
        sub = ex.get_ifd(TAG_EXIF_IFD)
        sub[TAG_DATETIME_ORIGINAL] = when
        sub[TAG_LENS_MODEL] = "FE 24-70mm F2.8 GM II"
        p = os.path.join(tmp, name + ext)
        im.save(p, pilfmt, exif=ex.tobytes())
        return p

    srcs_alt = []
    for k, (ext, pilfmt, model, when) in enumerate([
            (".jpg", "JPEG", "ILCE-7M4", "2026:05:01 10:20:30"),
            (".tif", "TIFF", "ILCE-7RM5", "2026:05:01 10:25:30"),
            (".png", "PNG", "Canon EOS R5", "2026:05:01 10:31:00")]):
        p = _make_exif_src("e%d" % (k + 1), ext, pilfmt, model, when)
        w, h, pf = probe_image(p)
        s = SourceImage(p, os.path.basename(p), w, h, pf, sha256_file(p))
        attach_exif(s)
        srcs_alt.append(s)

    for s in srcs_alt:
        if not s.exif_b64:
            log("    !! %s 提取不到 EXIF 块（kind=%s）" % (s.name, s.exif_kind or "-"))
            ok = False
    log("[4] EXIF 提取: " + "、".join(
        "%s[%s|%s]" % (s.name, s.shot_time or "-", s.model or "-") for s in srcs_alt))

    plans_alt = pack_canvases(srcs_alt, PIXCAKE_SAFE_LIMIT)
    exif_full = {"jpeg": True, "tiff": False, "png": True}
    for outfmt in ("jpeg", "tiff", "png"):
        for pl in plans_alt:
            out = os.path.join(tmp, "E_c%d_%s.tif" % (pl.index + 1, outfmt))
            compose_canvas(pl, srcs_alt, out)
            man = build_manifest(pl, srcs_alt, out, 12, 0, "E", False, PIXCAKE_SAFE_LIMIT)
            write_manifest(man, out)
            rep = split_canvas(out, man,
                               os.path.join(tmp, "eb_%s_%d" % (outfmt, pl.index)), fmt=outfmt)
            for it, o in zip(man["items"], rep["outputs"]):
                if not o.get("file"):
                    log("    !! %s 切分失败：%s" % (outfmt, o.get("error")))
                    ok = False
                    continue
                tags = read_exif_tags(o["file"])
                if tags["make"] != "SONY" or tags["model"] != it.get("model"):
                    log("    !! %s/%s 机型丢失：%s" % (outfmt, o["name"], tags))
                    ok = False
                if not tags["shotTime"]:
                    log("    !! %s/%s 拍摄时间丢失" % (outfmt, o["name"]))
                    ok = False
                if not o.get("exifCopied"):
                    log("    !! %s/%s 未报告写回 EXIF" % (outfmt, o["name"]))
                    ok = False
                with Image.open(o["file"]) as chk:
                    gex = chk.getexif()
                    if gex.get(TAG_ORIENTATION) not in (None, 1):
                        log("    !! %s/%s Orientation 未归一" % (outfmt, o["name"]))
                        ok = False
                    sub = gex.get_ifd(TAG_EXIF_IFD)
                    if exif_full[outfmt] and not sub.get(TAG_LENS_MODEL):
                        log("    !! %s/%s ExifIFD 子目录未带出（镜头）" % (outfmt, o["name"]))
                        ok = False
    log("[5] EXIF 写回校验完成（jpeg/png 完整 EXIF；tiff 仅 IFD0，子目录为 Pillow 限制）")

    # [6] row / col 版式：必须按原始像素排（1:1 无损），且不超单边上限
    for axis in ("row", "col"):
        for pl in pack_strip(srcs, PIXCAKE_SAFE_LIMIT, axis=axis):
            if pl.width > PIXCAKE_SAFE_LIMIT or pl.height > PIXCAKE_SAFE_LIMIT:
                log("    !! %s 画布超上限 %d×%d" % (axis, pl.width, pl.height))
                ok = False
            for p in pl.placements:
                s = srcs[p.src_index]
                if (p.w, p.h) != (s.natural_w, s.natural_h):
                    log("    !! %s 「%s」占位 %d×%d ≠ 原图 %d×%d（被缩放了）"
                        % (axis, s.name, p.w, p.h, s.natural_w, s.natural_h))
                    ok = False
            for i in range(len(pl.placements)):
                for j in range(i + 1, len(pl.placements)):
                    a, b = pl.placements[i], pl.placements[j]
                    if not (a.x + a.w <= b.x or b.x + b.w <= a.x or
                            a.y + a.h <= b.y or b.y + b.h <= a.y):
                        log("    !! %s 出现重叠" % axis)
                        ok = False
            out = os.path.join(tmp, "S_%s_c%d.tif" % (axis, pl.index + 1))
            compose_canvas(pl, srcs, out)
            man = build_manifest(pl, srcs, out, 12, 0, "S", False, PIXCAKE_SAFE_LIMIT)
            write_manifest(man, out)
            rep = split_canvas(out, man,
                               os.path.join(tmp, "sb_%s_%d" % (axis, pl.index)),
                               fmt="png", keep_exif=False)
            for it, o in zip(man["items"], rep["outputs"]):
                a = Image.open(o["file"]).convert("RGB")
                b = Image.open(it["source"]).convert("RGB")
                d = int(np.abs(np.asarray(a, np.int16) - np.asarray(b, np.int16)).max())
                if d != 0:
                    log("    !! %s/%s 切回偏差 %d" % (axis, o["name"], d))
                    ok = False
    log("[6] row/col 按原始像素排布 + 切回校验完成")

    # [6b] 超单边上限要自动拆画布；单张放不下要报错
    fakes = [SourceImage(os.path.join(tmp, "fake%d.jpg" % i), "fake%d" % i, 2000, 1500)
             for i in range(6)]
    sp = pack_strip(fakes, 5000, axis="row")
    if len(sp) != 3 or any(p.width > 5000 for p in sp):
        log("    !! row 自动拆画布异常：%d 张画布，宽 %s"
            % (len(sp), [p.width for p in sp]))
        ok = False
    try:
        pack_strip([SourceImage(os.path.join(tmp, "huge.jpg"), "huge", 20000, 1500)],
                   5000, axis="row")
        log("    !! 单张超上限时没有报错")
        ok = False
    except ValueError:
        pass
    log("[6b] row/col 超上限自动拆画布 + 单张拒绝校验完成")

    # [6c] 导入下限 100px：极小照片会把画布拖到下限以下，必须提前报错
    for fn, kw in ((pack_strip, {"axis": "row"}),
                   (pack_canvases, {})):
        try:
            fn([SourceImage(os.path.join(tmp, "tiny.jpg"), "tiny", 40, 40)], 5000, **kw)
            log("    !! %s 未拦截小于 100px 的画布" % fn.__name__)
            ok = False
        except ValueError:
            pass
    log("[6c] 画布低于导入下限时拒绝校验完成")

    # [7] 朝向与旋转：默认零旋转（人脸朝上）＝「竖屏竖着排、横屏横着排」；
    #     开旋转后画布数只应减少或不变，且所有画布仍然合法
    def _fake_batch(tag, w, h, n):
        return [SourceImage(os.path.join(tmp, "%s%d.jpg" % (tag, i)),
                            "%s%d" % (tag, i), w, h) for i in range(n)]

    for tag, (w, h), cnt in (("L", (7008, 4672), 6), ("P", (4672, 7008), 6)):
        batch = _fake_batch(tag, w, h, cnt)
        no_rot = pack_canvases(batch, PIXCAKE_SAFE_LIMIT, allow_rotate=False)
        rot_on = pack_canvases(batch, PIXCAKE_SAFE_LIMIT, allow_rotate=True)
        for pl in no_rot:
            for p in pl.placements:
                if p.rotation != 0 or (p.w, p.h) != (w, h):
                    log("    !! %s 默认不应旋转：rotation=%d 占位 %d×%d"
                        % (tag, p.rotation, p.w, p.h))
                    ok = False
        for pl in no_rot + rot_on:
            for e in verify_plan(pl, batch, PIXCAKE_SAFE_LIMIT, 12):
                log("    !! [7]%s %s" % (tag, e))
                ok = False
        if len(rot_on) > len(no_rot):
            log("    !! %s 开旋转后画布数反而变多：%d → %d"
                % (tag, len(no_rot), len(rot_on)))
            ok = False
        log("[7] %s %d×%d ×%d：不旋转 %d 画布 / 允许旋转 %d 画布"
            % (tag, w, h, cnt, len(no_rot), len(rot_on)))

    # [7b] 横竖混排的密度优势：A7M4「2 横 + 1 竖」不靠旋转就该拼进同一张画布
    mix = _fake_batch("mixL", 7008, 4672, 2) + _fake_batch("mixP", 4672, 7008, 1)
    mp = pack_canvases(mix, PIXCAKE_SAFE_LIMIT, allow_rotate=False)
    if len(mp) != 1 or len(mp[0].placements) != 3 or mp[0].width > PIXCAKE_SAFE_LIMIT \
            or mp[0].height > PIXCAKE_SAFE_LIMIT:
        log("    !! 2 横 + 1 竖 应拼进 1 张画布，实得 %d 画布" % len(mp))
        ok = False
    log("[7b] 2 横 + 1 竖（A7M4，不旋转）-> %d 张画布" % len(mp))

    log("[SELF-TEST]", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(self_test())
