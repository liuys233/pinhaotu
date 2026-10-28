# 拼好图 · 优化计划：EXIF 全链路 + 同场同光把关

## Context（为什么做这次改动）

拼好图是给摄影师省像素蛋糕额度的工具：把 N 张照片拼成一张画布 → 精修一次 → 按 manifest 切回 N 张。
"拼合 → 精修 → 切回"这条主链路当前版本已经跑通，问题不在**能不能**，而在**切回来之后这张照片还是不是"摄影师的原始文件"**。

用户（摄影师）的取舍已经明确：

| 决策点 | 结论 |
|---|---|
| 分组策略 | **保持自动最少画布数**（省额度优先），不改成固定 2/3/4/6 张 |
| 元数据 | **EXIF 全套**，保留现状并加强；不要求 ICC / IPTC / XMP / 文件名模板 |
| 串味风险 | **人工把关**：同场同光的照片才放同一批，需要软件把判断依据显示出来 |
| 使用规模 | 一场十几张以内（婚礼 / 写真单场） |

因此本次要解决两件事：

1. **EXIF 全链路可靠**——任何格式的源图进、任何格式的结果出，拍摄信息都不能丢。
2. **把"同场同光"的判断依据摆到界面上**——让用户自己看得见、判得准。

---

## 已确认问题（实测，非推测）

### P1 源图不是 JPEG 时，EXIF 全丢（核心缺口）

[tiler_core.py](file:///d:/xsdg/像素蛋糕/拼好图/tiler_core.py#L172-L183) 的 `extract_exif_app1()` 第一行就判 JPEG 魔数 `\xff\xd8`，非 JPEG 直接返回 `None`。

实测：

```
源是 TIFF 时，extract_exif_app1 返回： None
但 PIL 读回的 0x010F/0x0110/0x0132 = SONY / ILCE-7M4 / 2026:05:01 10:20:30
e2.tobytes() 长度: 262
```

后果：摄影师若拿 **TIFF 源图**（相机直出 TIFF，或从别处导出的 TIFF）走一遍流程，切回的 JPEG **拍摄时间 / 机身 / 镜头 / ISO 全空**，但软件不报任何错。这正好命中"要把原来的数据写回去"的需求。

### P2 只有 JPEG 输出会写 EXIF

[split_canvas](file:///d:/xsdg/像素蛋糕/拼好图/tiler_core.py#L696-L711) 里 `keep_exif` 分支写死 `if outfmt == "jpeg"`。用户把切回格式选成 `tiff` / `png` / `original`（原图是 TIFF 时）→ **EXIF 同样丢失，且无告警**。

### P3 没有拍摄时间，无法判断"同场同光"

`SourceImage`（[tiler_core.py](file:///d:/xsdg/像素蛋糕/拼好图/tiler_core.py#L270-L291)）只存尺寸和 sha256，没有拍摄时间。
照片列表（[tiler_gui.py](file:///d:/xsdg/像素蛋糕/拼好图/tiler_gui.py#L274-L279)）只有「文件 / 尺寸」两列。
排版结果只显示画布尺寸和填充率，**看不出这张画布把哪几个时间点的照片混在了一起**。

### P4 机型识别只靠像素尺寸

`detect_camera()`（[tiler_core.py](file:///d:/xsdg/像素蛋糕/拼好图/tiler_core.py#L119-L125)）按 `w/h` 猜，镜头裁切或机内裁切后会识别失败。EXIF 里本来就有 `Model`(0x0110)，现成没用。

---

## 技术可行性（已实测，Pillow 12.2.0）

| 验证项 | 结果 |
|---|---|
| `Image.Exif.tobytes()` 的输出 | **自带 `Exif\0\0` 头**（`head=b'Exif'`），可直接作为 payload |
| PNG / TIFF / WEBP `save(exif=blob)` | 三种都能写入并读回 Make/Model/DateTime |
| JPEG 段注入 `FF E1 + len + payload` | Make/Model/DateTime/Orientation 全部读回正确 |
| 改 Orientation、pop 缩略图 tag 后再 `tobytes()` | 正常 |
| TIFF/PNG 源的 `getexif()` | 能读到 0x010F / 0x0110 / 0x0132 |

结论：**不需要引入新依赖**，Pillow 自身能力足够。

---

## 方案

### 一、`tiler_core.py`：EXIF 提取统一入口（双通道，互不干扰）

保留现有 JPEG 通道**完全不动**（它靠手写字节操作，能保住厂商私有 MakerNote，已被生产验证）：

- 保留 [extract_exif_app1](file:///d:/xsdg/像素蛋糕/拼好图/tiler_core.py#L172-L183) + [_strip_thumb_normalize](file:///d:/xsdg/像素蛋糕/拼好图/tiler_core.py#L186-L221)
- 保留 [inject_exif_into_jpeg](file:///d:/xsdg/像素蛋糕/拼好图/tiler_core.py#L229-L246) 的 APP1 段注入逻辑

新增一条**非 JPEG 通道**（Pillow 原生）：

- 新增 `read_exif_tags(path) -> dict`：统一用 `Image.open(path).getexif()` 读
  `Make(0x010F)` / `Model(0x0110)` / `DateTimeOriginal(0x9003)` / `DateTime(0x0132)` / `LensModel(0xA434)` / `Software(0x0131)`。JPEG / TIFF / PNG 一视同仁。
- 新增 `extract_exif_blob(path) -> bytes | None`：
  - 源是 JPEG → 走老路（`extract_exif_app1` + `_strip_thumb_normalize`），返回**完整 APP1 段**（含 `FF E1` + 长度），即现有 `seg` 的语义，保持 `inject_exif_into_jpeg` 调用点不变。
  - 源是其它格式 → `Image.Exif().load(Image.open(path).getexif().tobytes())`，把 `0x0112` 置 1、pop `0x0201`/`0x0202`（缩略图偏移），再 `tobytes()`。返回**裸 EXIF 块**（自带 `Exif\0\0` 头），供 `save(exif=...)` 使用。
  - 用一个显式类型标记区分（如返回 `(blob, kind)`，`kind in {"app1","raw"}`），避免两条通道的字节格式被写串——这是本方案最容易出错的一点。
- 新增 `apply_exif_to_output(out_file, blob, kind, outfmt)`：把写回逻辑收口到一处
  - `jpeg` + `kind=="app1"` → 复用 `inject_exif_into_jpeg`
  - `jpeg` + `kind=="raw"` → 拼成 `FF E1 + len + blob` 段后注入（实测可行）
  - `tiff` / `png` / `webp` → 在保存时传 `exif=blob`
  - 这样 `split_canvas` 里只留一行调用，不再散落 `if outfmt == "jpeg"`。

### 二、`tiler_core.py`：manifest 带上 EXIF 与拍摄时间

用户的实际工作流里，原图大概率会被归档 / 改名 / 移动。当前切回时是从 `source` 路径**重读**源图取 EXIF（[tiler_core.py](file:///d:/xsdg/像素蛋糕/拼好图/tiler_core.py#L713-L723)），源图一旦不在，EXIF 就没了。

- `SourceImage` 增加字段：`shot_time: str`、`model: str`、`exif_b64: str`（在导入照片时一次提取并缓存）。
- `build_manifest()` 的每个 item 增加 `shotTime` / `model` / `exifB64`。
- `MANIFEST_VERSION` 4 → **5**；`split_canvas` 读 v4 时 `exifB64` 为空，自动回退到老逻辑（读 `source` 路径），保证旧 manifest 仍可用。
- 切回时优先级：manifest 内嵌 `exifB64` → 源文件路径 → 都没有则记 `exifNothingToCopy` 并在日志里提示。

体积代价：Sony 带 MakerNote 的 APP1 约 30–70KB，base64 后 ×1.33；一张画布 6 张 ≈ 400KB。
放在一份 30–80MB 的 TIFF 旁边可以接受，换取"原图不在也能写回"。

### 三、`tiler_core.py`：机型识别改用 EXIF

`detect_camera()` 保留（作为兜底），新增 `detect_camera_exif(make, model)` 优先解析 `ILCE-7M4` / `Canon EOS R5` / `X-T5` 之类的型号串，映射到 `CAMERA_PRESETS` 的 key。
命中失败再退回按尺寸猜。改动局限在 `detect_camera` 与 `add_paths` 两处调用点。

### 四、`tiler_gui.py`：把"同场同光"的判断依据摆出来

1. **照片列表加「拍摄」列**
   [tiler_gui.py](file:///d:/xsdg/像素蛋糕/拼好图/tiler_gui.py#L274-L279) 现在是 `#0 文件(186) + size(86)`。
   改为 `#0 文件(150) + size(80) + time(96)`，时间显示 `MM-DD HH:MM`。
   左列宽度 330 → 372（同步改 [L253](file:///d:/xsdg/像素蛋糕/拼好图/tiler_gui.py#L252-L255) 的 `minsize` 和 [main()](file:///d:/xsdg/像素蛋糕/拼好图/tiler_gui.py#L1129-L1131) 里的窗口宽度计算，避免按文档 §6.2 的老坑把右列挤出去）。

2. **「按拍摄时间排序」按钮**
   放在照片卡片工具条（[L285-L289](file:///d:/xsdg/像素蛋糕/拼好图/tiler_gui.py#L285-L289)）。
   重排 `self.srcs` 并刷新 Treeview。**不改变排版结果**——`pack_canvases` 自己按面积排序，列表顺序只影响人的阅读顺序。

3. **画布时间跨度提示**
   在 [render()](file:///d:/xsdg/像素蛋糕/拼好图/tiler_gui.py#L735-L741) 里算当前画布所有 placement 的 `shot_time` 跨度，写进 `card_canvas.set_subtitle`：
   - 跨度 ≤ `SAME_SCENE_MAX_MINUTES`（新常量，默认 **30 分钟**）→ 正常显示
   - 超出 → 副标题变橙并追加 `⚠ 时间跨度 2 小时` 
   这样用户在第一眼就能看出"这张画布混了不同场次"，自己决定要不要拆。

   > 阈值 30 分钟是启发式常量，写在 `tiler_core` 里方便改。若你觉得该按"相机连续号段"而不是时间，实施时说一声，我换成相邻序号判断。

---

## 本次不做（明确划出边界，避免顺手改坏）

| 事项 | 原因 |
|---|---|
| ICC 色彩配置按图各自搬 | 用户未选。当前只取第一张的 ICC，多色彩空间混拼时确实不严谨，但改动面大，单独排期 |
| IPTC / XMP / 文件名模板 | 用户未选 |
| ~~`row` / `col` 版式**静默重采样**（按 `min(高)` 缩放，manifest 记的 crop 尺寸 ≠ 原图尺寸，破坏 1:1 承诺）~~ | **已在后续阶段修复**：新增 `core.pack_strip`，按原始像素放置、交叉轴居中留白，切回逐像素偏差 = 0 |
| ~~`row` / `col` 不校验单边上限（10 张 6000px 横排 = 60000px 画布照导不误）~~ | **已在后续阶段修复**：`pack_strip` 校验单边上限，超限自动拆多张画布；单张放不下抛 `ValueError` |
| 拖拽后可超出单边上限、无撤销（[_overlap_check](file:///d:/xsdg/像素蛋糕/拼好图/tiler_gui.py#L886-L887)） | 与本次目标无关 |
| `Worker._drain` 关闭后 `after()` 无 `closing` 保护（[L79](file:///d:/xsdg/像素蛋糕/拼好图/tiler_gui.py#L79)）会抛 `TclError` | 一行修复，但不在本次范围 |
| 缩略图两次全解码导致多图卡顿、`pack_canvases` 贪心阶段无时间预算 | 用户规模是"十几张以内"，当前性能够用 |
| 会话恢复 / 手工画布 / manifest 内嵌进 TIFF | 文档 §10 待办，工作量大 |

---

## 验证方式

### 1. 引擎自检（扩展断言）

```bash
python tiler_core.py
```

在 `self_test()` 现有的 3 张 JPEG 之外，**新增**：
- 造一张带 EXIF 的 **TIFF 源图**与一张 **PNG 源图**，走 `pack → compose → split`
- 断言切回结果能读回 `Make / Model / DateTimeOriginal`，且 `Orientation == 1`
- 保留原有判据：**原样切回最大像素偏差必须 = 0**

### 2. 端到端（扩展覆盖）

```bash
python tests/e2e_test.py
```

在现有 7 个环节后**新增第 8 节**：对同一批照片分别以 `jpeg` / `tiff` / `png` 三种 `fmt` 切回，逐一断言 EXIF 三件套齐全。
其余判据不变（调色后结构相关 > 0.99、缩放映射、裁剪必须抛异常）。

### 3. UI 冒烟 + 截图

```bash
python tests/_ui_shots.py
```

新增一张截图验证「拍摄」列与画布时间跨度提示的实际观感。

### 4. 手工验收（真实素材，最关键）

用 **1 张真实 A7M4 JPEG + 1 张 TIFF** 走完整流程，切回后分别用
`exiftool`（若无则用 PIL `getexif()`）核对：

- [ ] `Make` / `Model` / `LensModel` / `ISOSpeedRatings` / `FNumber` / `ExposureTime` 与原图一致
- [ ] `DateTimeOriginal` 与原图一致
- [ ] `Orientation == 1`（竖拍交付不躺倒，对应文档 §6.8）
- [ ] 资源管理器里**看不到未修图的旧缩略图**（摘缩略图生效）

---

## 涉及文件

| 文件 | 改动 |
|---|---|
| [tiler_core.py](file:///d:/xsdg/像素蛋糕/拼好图/tiler_core.py) | EXIF 双通道提取、`SourceImage` 增字段、manifest v5、`split_canvas` 写回收口、机型识别 |
| [tiler_gui.py](file:///d:/xsdg/像素蛋糕/拼好图/tiler_gui.py) | 照片列表加「拍摄」列、排序按钮、画布时间跨度提示 |
| [tests/e2e_test.py](file:///d:/xsdg/像素蛋糕/拼好图/tests/e2e_test.py) | 新增第 8 节：三种输出格式的 EXIF 往返 |
| [README.md](file:///d:/xsdg/像素蛋糕/拼好图/README.md) / [docs/开发文档.md](file:///d:/xsdg/像素蛋糕/拼好图/docs/开发文档.md) | 补充 manifest v5 的 `exifB64` 字段说明与兼容策略 |

> `tiler_ui.py` 不改动；现有 `Card` / `Field` / `ScrollArea` 组件直接复用。
