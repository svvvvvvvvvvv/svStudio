# svFilm

数码仿胶片引擎（重写版）。**两条链**：
- **C1（生产，默认）**：RAW/JPG → 解码 + 白平衡 + 护栏 → 判场景 → spektrafilm（物理胶片链）→ **冲印成片**。
- **C2（`lrbase`，LR 分工）**：RAW → 解码 → spektrafilm 负片 → scan_film → **平基底片（16-bit TIFF）+ LR 元数据**；印相/影调/分色交给 LR。

> 本目录是 `<svStudio 仓库>/svFilm/`（git subtree 合进来的）⇒ 拉 svStudio 一个仓库就够。
>
> `lrfinish/` = **LR 收尾**（原 `E:\lr_agent`，2026-10-23 并入），不是引擎；见 `lrfinish/README.md`。
>
> **预设统一**：`svFilm/data/presets/<名字>/` 同时放 `preset.json`（胶片，引擎读）和 `finish.json`（LR 收尾，lrfinish 读）。
>
> ★ **知识分工**（别混）：
> · **本文件** = 怎么用 + 模块边界 + 契约
> · **`引擎手册.md`** = 引擎机制（参数作用形式 · 键语义 · 死键 · 已证否的路）
> · **`迭代记录.md`** = 哪一轮改了什么
> · **代码注释** = 只写机制与一般工程（**不写过程、不写日期**）

---

## ★ 对外入口 —— svFilm 是一个「引擎」，不是应用

> **能力 = 喂一张图 + 一组参数 → 出一张图。** 没有界面、没有弹窗、不假设"用户在看"。

| 口子 | 形态 | 谁用 |
|---|---|---|
| **CLI（C1）** | `python -m svFilm.cli one <图> --stock 鹿井预设` | 脚本 / 一次性任务 |
| **CLI（C2）** | `python -m svFilm.cli lrbase <图/目录> -o <out> --base-curve ...` | LR 分工 / 交付平基底片 |
| **本地 HTTP 服务** | `python -m svFilm.service`（**常驻**，只绑 `127.0.0.1:8765`） | 工作台 svStudio |

★ **几个口子共用同一套算法**（CLI 是薄壳；C2 只是把链路停在 scan_film，不多写一套）。
★ ⚠ **绝不允许「每次调用起一个进程」**：`io.load_raw` 一个人占全链一半时间，
  常驻 + 解码缓存是硬要求（冷启动 5.0 s vs 常驻 1.45 s）。

## 跑起来之前

```bash
# 只装这些 —— 胶片引擎 spektrafilm 已经随仓库带在 _tools/spektrafilm/，不用自己装
pip install numpy Pillow opencv-python rawpy colour-science mediapipe
# C2 lrbase 的 16-bit TIFF 还需要：
pip install tifffile
```

⚠ 引擎找 spektrafilm 的顺序（`spektra.py` 的 `_sf()`）：
① 环境变量 `SPEKTRAFILM_ROOT` → ② **`_tools/spektrafilm/src`（仓库自带，默认走这条）**
→ ③ 上一级的 `_tools/spektrafilm/src`。三个都没有会直接报错并列出试过的路径。
★ 它还会**钉死加载的是这一份**：本机若有另一份 editable 安装（`.pth`），不走 `_sf()` 会**静默拿到另一份**。

## 模块地图

| 模块 | 管什么 |
|---|---|
| `io.py` | **入口/出口**：RAW/JPG → 场景线性（白平衡 · 色彩空间 · `MAX_SIDE`）。唯一允许出现"机型"的地方 |
| `color.py` | 色彩基元（`l2s` / `to_lab` 等），自实现 |
| `cameras.py` | **IDT 机型表**：加机型不改代码，只加一行 |
| `scene.py` | **场景判据**：`exp` / `span` / `back` / `overwhite` 四轴 + 版本化 `key`；判不出 ⇒ **弃权（None）** |
| `lighttype.py` | **光型**（二维连续量）：逆光 / 面光 / 缝隙光 |
| `person.py` | **人在哪**：低开销粗掩膜（256×256 `selfie_multiclass`），**只服务光位判断**，不做人脸识别 |
| `exposure.py` | **曝光主人的落点**：中灰锚 `ev' = (1-w)·ev + b` |
| `presets.py` | 预设 JSON → spektrafilm 参数 → 渲染入口；人本测光（`subject`）；参数缓存 |
| `spektra.py` | spektrafilm 装载器 + 大图条带包装（**不在这里选卷/配纸/调旋钮**） |
| `scanfx.py` | 扫描段补偿（色度模糊等），**不动 vendored 引擎** |
| `targets.py` | 靶 + **场景 → 引擎参数覆盖**（`{"mul"}` / `{"add"}` / `{"by"}` 三套语义） |
| `pipeline.py` | 编排一条链 + 段缓存（键里**必须含靶/场景覆盖**） |
| `service.py` | 常驻 HTTP 服务（路由见文件头） |
| `cli.py` | 命令行入口（见下） |
| `scorecard.py` | 评分卡：每次改动对**同一套指标**打分；读 `targets.json` 的 `span` 与 `_lc_band` |
| `hist.py` | LR 风格直方图工具 |
| `lrbase.py` | **C2**：scan_film → 线性 → 反转/归一 → 16-bit TIFF（平基底片） |
| `lr_meta.py` | **C2** 元数据：JSON / XMP sidecar / lrpreset / manifest |
| `config.py` | **全部可调参数**（别处不许写死数） |
| `selftest.py` | 自检：`python -m svFilm.selftest`（纯合成图 + 不变量，不依赖样片）|
| `paths.py` | 产出路径规范（效果图/调试产物一律走它） |

**已删除的层（不要再叠回来）**：`tone.py`（影调）· `grade.py`（颜色）· `face.py` / `facegain.py` / `region.py`（认人认脸）。
⇒ **曝光 / 反差 / 颜色全归引擎**；svFilm 这一层只做「看哪里（测光 / 场景）」与「接线」。

## 契约（改任何一层前先看）

```
★ **喂场景线性、出显示域**（`io.output_color_space='sRGB'` + `output_cctf_encoding=True`）。
★ **预设 = `data/presets/*.json`**：现在**只有一条**（`鹿井预设.json`）——
  值 = 引擎 schema 默认 + 两根接线钉（`scan_white/black_correction=True`）。
★ **靶 / 覆盖在 `data/targets.json`**，层序：`_scene_engine["*"]` → `_stock_engine[预设]` → 按轴覆盖
  （**后者盖前者**）。当前基线只剩 `morph.active`（结构性必需：预设 JSON 里没有这个键，
  schema 默认 False ⇒ 没它整根 `density_curves_morph` 静默死）。
★ **`presets.render()` 裸调不读 `targets.json`** ⇒ 生产路径 =
  `targets.scene_engine(sc, stock, cfg)` 之后作为 `overrides` 传入。
★ **`overrides` 必须整份传**：只传"要改的那一根"会把基线丢掉。
★ **曝光三处分工**：`camera.auto_exposure`（引擎锚 0.184）· `exposure.py` 中灰锚 ·
  `enlarger.print_exposure`（**整卷常数**）。「逐图算曝光」只能在 `exposure.py` 那一层做。
★ **空间族（颗粒 / 柔光 / halation）都在引擎里**，svFilm 不许外挂一层。
★ **C2 = `lrbase`**：只调 `presets.render_linear`（`scan_film` / 线性 / 不裁剪），
  输出平基底片；**不许改 C1 的 `render` / `render_person_pe` 路径**。
```

## 常用命令

```bash
python -m svFilm.cli one   <图> --stock 鹿井预设    # 单张
python -m svFilm.cli dir   <目录> --jobs 4          # 批处理（重活建议 <=4：内存）
python -m svFilm.cli lrbase <图/目录> -o <输出目录> --jobs 2 \
  --mode flat|print_neutral --grain-owner svfilm|lr --halation-owner svfilm|lr \
  --master svFilm/data/lr/print_neutral_master.json   # C2：平基底/中性印相 + LR 元数据
python -m svFilm.cli lrbase-fit --print <C1成片> --flat <平基底TIFF> \
  --out <base_curve.json>                           # C2：拟合基底曲线
python -m svFilm.cli lrbase-preview --flat <平基底TIFF> \
  --curve <base_curve.json> --out <预览.jpg>         # C2：不开 LR 看“平基底+曲线”
python -m svFilm.cli probe <图>                     # 只分析、不写文件
python -m svFilm.cli stocks                         # 列出可用预设
python -m svFilm.cli calib <RAW+JPG 同名对目录>       # 量机型表要填的基线增益

python -m svFilm.service                            # 常驻服务（127.0.0.1:8765）
python -m svFilm.scorecard <目录>                    # 评分卡
python -m svFilm.hist <图>                          # 直方图
python -m svFilm.selftest                           # 自检（动完任何一层都要跑）
python -m svFilm.lrbase_selftest                    # C2 自检（合成图，不依赖样片）
```

## C2：LR 分工（`lrbase`）

> **一条分支、一个口子**：C1（`one` / `dir` / `service`）一个字节不改；C2 走 `lrbase`。
> **v2 分工**：svFilm 管胶片介质，LR 管创作意图；`owners` 写在 metadata 里。

```
mode=flat：
RAW → io.load → render_linear（scan_film，线性、不裁剪）
    → 反转/归一（线性域倒数 + 逐通道分位 + 可选增益/留白）
    → 16-bit TIFF（**不带 ICC**）→ lr_meta（owners: medium_paper=lr）

mode=print_neutral：
RAW → io.load → render（固定 print_exposure，**不跑 person_pe / 不认人**）
    → 中性印相（stock + paper + scan + grain + halation）
    → 16-bit TIFF（**不带 ICC**）→ lr_meta（owners: medium_paper=svfilm, master_*=lr）

mode=full：C1 `one`/`dir` 已有，lrbase 不重复实现。
```

**产物**（`<stem> = 原文件名 + _svFilm`）：

| 文件 | 内容 |
|---|---|
| `*_svFilm_flat.tif` | `flat` 模式的平基底片；给 LR 做印相/影调/分色 |
| `*_svFilm_print.tif` | `print_neutral` 模式的中性印相；LR 只做大师层 |
| `*.svfilm.json` | 全量 recipe + **owners/baked** + 最终扁平 develop settings |
| `*.xmp` / `*.svfilm.lua` / `*.svfilm.lr.txt` / `*.lrpreset.xmp` | LR 应用形式（推荐 `.lr.txt`） |
| `_svfilm_manifest.json` | 批量清单（路径/hash/preset/mode） |

**模式与 owner**：

- `flat`：负片段（卷/颗粒/halation/扫描）留在 svFilm；相纸/印相/大师归 LR；
- `print_neutral`：相纸/印相也归 svFilm（中性、固定印相曝光），但 **master 曝光/反差/分色仍归 LR**；
- `--grain-owner lr` / `--halation-owner lr`：显式把颗粒/halation 让给 LR（默认 svfilm）。

**LR master 起点**：

- `svFilm/data/lr/flat_master_K2.json`（flat：Basic + 黄色降饱和）
- `svFilm/data/lr/print_neutral_master.json`（print-neutral：只做肤色，曝光逐图给）

**⚠ 元数据是“扁平表”**：LR 只有一条 tone curve，基底曲线与大师曲线已在生成端 compose；
`components` 只作溯源，**不要**按顺序二次套 preset。`baked.grain=true` ⇒ LR 里 `GrainAmount=0`。

**⚠ 16-bit TIFF 不写 ICC**：LR 15.1 对 16-bit+ICC 会渲染成青蓝；未标记 RGB 按 sRGB 解释。

**⚠ 现在仍用 `--stock` 的完整预设**（含该预设的 WB/PE/`amount`）⇒ 平基底片里已经带了“大师的印相配平”，
`lrbase-fit` 拟合出来的曲线复现的是“该预设的冲印”，不是“中性印相”。真正的 film-only preset（卷/纸与大师分离）
是 C2 的下一步；在那之前，`lrbase` 是一个**可用的过渡**，不是最终分工。

**⚠ 依赖**：`tifffile`（`pip install tifffile`）。没有它时 `lrbase` 会报错，C1 不受影响。

**LR 侧**：`E:\lr_agent\lrplugin\SvFilmBridge.lrplugin`（草案）读 `*.svfilm.lr.txt`，
`Library → Plug-in Extras → svFilm: Apply metadata`；也可用 Automaat MCP 读 `*.svfilm.json`。
插件**未在 LR 里实际加载过**，先用虚拟副本试。

**基底曲线**：`lrbase-fit --print <C1成片> --flat <平基底TIFF>` 拟合 `平基底 → 冲印` 的
逐通道 16 点曲线；输出 JSON 给 `lrbase --base-curve`。正式标定要**同内容、同像素**的一对图，
print 用 PNG/无损（JPEG 会带压缩噪声），并尽量用 16-bit 平基底。单对图只验证“形状能表达”，
不是生产标定。

## 数据文件

| 文件 | 内容 |
|---|---|
| `data/presets/鹿井预设.json` | 唯一预设（值 = 引擎默认 + 两根接线钉）|
| `data/targets.json` | 基线 + 该预设的靶（26 张逐图 `[P25,P75]`）+ 大师 `lc` 带 |
| `data/_archive/` | 清账前的原样快照（旧 10 条预设 · 旧 targets）|

## 许可

* **svFilm 本体**：见仓库根 `LICENSE`。
* **vendored `spektrafilm`**：profiles 与生成的 LUT = **CC BY-SA 4.0**（署名 + 相同方式共享）
  ⇒ **与它一起分发时，那一份组合分发要遵守 CC BY-SA 4.0**。详见 `_tools/README.md`。
