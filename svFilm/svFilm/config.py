# -*- coding: utf-8 -*-
r"""svFilm 的全部可调参数 —— **一个文件，别处不许写死数**。

⚠ 改这里的数**会影响所有出图**。做实验别改文件：
  `from svFilm import config as C; C.XXX = v` —— 且必须**先于** `import pipeline`。

## 一句话现状

**一条链**：`解码 + 白平衡 + 护栏 → 判场景 → 胶片引擎(spektrafilm 0.3.4) → 出图`
⇒ **引擎出图 = 成片**；只有引擎动像素（唯一例外是 `presets._post_scan` 的 `scanfx.chroma_blur`，
那属于**扫描段**）。影调层 / 肤色层 / 认人认脸 / 颜色层**已整段删除**。
⚠ 新增旋钮**名字要落在 `CACHE_KEY_PREFIXES` 的前缀里**，否则不进缓存指纹 = "拧了没反应"。

## 层纪律（"一个量、一个主人"）

| 视觉量 | 唯一主人 |
|-------|---------|
| 相机转换 / 白平衡 | 入口（`PUBLIC_WB*`）|
| **整张落点（曝光）** | **引擎**（`camera.auto_exposure` + `enlarger.print_exposure`）|
| **整张跨度（反差）** | **引擎**（H&D 曲线；实际兜住它的是 `print_render.chemistry`）|
| **高光放开（允许白）** | **引擎**（`lightness_compression`）|
| 胶片物理 · 质感（颗粒/光晕/柔光/锐化）| 引擎（spektrafilm dev 6cd00c8）|
| `print_y_filter_shift` | ★ 它是**整张**配平，不是"脸"的旋钮 |

★ 引擎里有**【两处】高光保护**（扫描末端的 `lightness_compression` ＋ 拍摄前的
  `camera.auto_exposure_method` 高光加权）⇒ **任何"放开高光 / 提亮"的动作，先问"保护机制还剩几个"**。

## 死键清单（改了没反应）

| 死的东西 | 为什么死 |
|---|---|
| 预设 JSON 的 `load_raw` 整段 | `presets._apply()` 一个字节都不读 |
| `camera.diffusion_filter.*` / `grain.micro_structure` | `active=False` / 两道内门（σ>0.05 且 blur>0.4px）|
| `film_render.glare.*` | 管线只取 `print_render.glare` |
| `halation.*` 的 σ/strength、`dir_couplers` 的 4 个 `gamma_*` | 只在 `apply_stocks_specifics=True` 那条路被重写（`params_builder.py:186-265`）；`presets` 传 `False` ⇒ **预设值活** |
| `print_render.density_curve_gamma` / `density_curves_morph` / `film_render.density_curve_gamma` | dev 均**无此字段**（→ 两端 `chemistry`）⇒ 写了是**静默 no-op** |
| `enlarger.lens_blur` / `debug.print_timings` | 引擎里**零读取点** |
| `enlarger.preflash_*_filter_shift` 的 `shift` 侧 | 预闪**只读 `neutral + preflash_shift`** |

**⇒ 排查"改了没反应"的顺序**：① 查这张表 ② 查 `settings.preview_mode`（True 会把颗粒/模糊/锐化
**全部清零**）③ 查字段名有没有随 vendor 版本改名（dev 已删一批，见引擎手册 §19.8）④ 才轮到缓存 / digest / 并发。
"""
from __future__ import annotations

import os

VERSION = '3.0'



def key_signature(mod=None):
    """把「会影响出图的 config 值」压成一个**可哈希、可比较**的指纹（用途：段缓存键 /
    `presets._params_for` 的缓存键 / 解码签名）。⚠ 不可哈希的退化成 `repr`（宁可靠保守作废缓存）。"""
    import sys as _sys
    m = mod if mod is not None else _sys.modules[__name__]
    out = []
    for k in dir(m):
        if k.startswith('_'):
            continue
        if k in CACHE_KEY_EXTRA or k.startswith(CACHE_KEY_PREFIXES):
            v = getattr(m, k)
            try:
                hash(v)
                out.append((k, v))
            except TypeError:
                out.append((k, repr(v)))
    return tuple(sorted(out))

# ===========================================================================
# 胶片风格（默认用哪一条）
# ===========================================================================
# ⚠ 名字**必须**在 `presets.names()` 里（= `data/presets/*.json` 的文件名），不认得**当场报错**。
STOCK = '鹿井预设'

# ===========================================================================
# 管线接法：喂给胶片引擎的输入
# ===========================================================================
# ★★ 只有一条路：`io.load_raw` → 场景线性 → 引擎（入口提亮归 `camera.auto_exposure`）；
#   下面的 `PUBLIC_*` 喂的是 **vendor 的 RAW 加载器**（白平衡），是活的。
# ★ 白平衡的判据**不许只看"整张中位"** —— 整张对齐 ≠ 分段对齐（只看整张会把中调/阴影整段推黄
#   而看不出来）；`PUBLIC_WB='as_shot'` 不用（实测偏差比 `custom` 大）。
PUBLIC_WB = 'custom'
# ★★ 10-16 重标（偏黄绿专题）：**色温 / 色调必须一起动** ——
#   ① 判据 = 「我们 − 同一张的机内 JPG」逐张算再取中位 ⇒ 5200 整张 b* +4.51 / 中调 +8.04（四张全正）= 染黄；
#   ② 量鹿井 26 张的中性轴 = **b* 全程贴 0 + a* 中低亮偏绿** ⇒ 单动色温治不了 a*（横的那根它不动）；
#   ③ 6 个组合臂实测：`4600 + 0.97` 两个毛病同时下去（b* +2.7→0 附近 / a* 中低亮 −3.8→−2.8 / 中性占比 40.3→45.2）；
#   ④ ⚠ **代价 = 整张 L 抬 ~+2.6**（降色温的副作用）⇒ 印相曝光那一层要跟着看。
#   出处：`E:\ds\photo\重标鹿井_1015\` 的 `交付_白平衡风格_1016.html` / `交付_色温色调组合_1016.html`。
PUBLIC_WB_TEMPERATURE = 4600.0
PUBLIC_WB_TINT = 0.97
PUBLIC_COLORSPACE = 'ProPhoto RGB'

# **入口解码快速通道**（`fastraw.py`）：RAW 只开一次 + 白点适应/tint/空间换算合成一次 3×3。
# ★ 数学等价（矩阵由 vendor 函数用探针像素生成，顺序与原链一致），26 MP 上省 ~10 s。
# ★ False ⇒ 回到 vendor 的 `load_and_process_raw_file`，行为与从前完全一致（出问题一键回退）。
FAST_RAW = True

# **磁盘缓存**（`diskcache.py`）：跨进程/重启，只存「与胶片参数无关」的东西 ——
# 解码结果（3.6 s/张）+ 判据结果（找人 2.4 s + 场景 3.1 s）。调参迭代时这三样都不变。
# ★ 关掉：`CACHE_ENABLE = False`（现成的总开关，StageCache 也一起关）。
RAW_CACHE = True                # 解码缓存
CACHE_DIR = None                # None ⇒ `<仓库>/.cache`；给空串 ⇒ 关掉磁盘缓存
CACHE_MAX_MB = 8000             # 超过就按最后访问时间清（8 GB ≈ 240 张 @2048）

# **扫描仪段补偿**（`scan_film=True` 时用；默认关）：上游 `color_reference.py` 在"扫负片"这条
#   路**故意不做黑白校正**（负片没有绝对黑白参考，那由冲扫流程与扫描仪软件定）⇒ 引擎输出没被锚定
#   （合成图实测：纯白 → L*32、中灰 b* +27）。本组键补「按片基定黑白点」+「**每通道**自动色阶」。
#   详见 `svFilm/scanfx.py`；**不动 vendored 的 spektrafilm**。
SCANFIX_ENABLE = False          # ← 打开才生效
SCANFIX_MODE = 'per_channel'    # 'per_channel'（模拟片基中性）| 'luma'（单条亮度拉伸）
SCANFIX_LO = 0.5                # 黑点取哪个分位（%）
SCANFIX_HI = 99.5               # 白点取哪个分位（%）
SCANFIX_CLIP = 0.0              # 拉伸后统一压一点，避免噪声顶成纯白（0 = 不压）

# **扫描色度模糊**：尺子「色度/亮度 锐度比」大师 **0.60**、我们 **1.03**（相机直出 0.71）⇒
#   我们的色度比大师**锐 70%**；在 **CIELAB 的 a*/b* 上**模糊 ⇒ 只动色度、不动细节，σ 用
#   **画面宽度的比例**（与分辨率无关）。
#   ★★★ 现值 `True` ⇒ **这一层在跑**（它一直在改画面的色度）；要不要关 = **目标性** ⇒ 待用户拍板。
#   实现见 `svFilm/scanfx.py` 的 `chroma_blur()`。
CHROMA_BLUR_ENABLE = True
CHROMA_BLUR_W = 0.0015          # σ = 该比例 × 画面宽度（0.0015 × 2000px ≈ 3px）





# ========== 输入 ==========
MAX_SIDE = 2048                 # 工作分辨率（长边）

# ★★★ `film_render.grain.blur` 的**标定基准长边**（改了会让预览 = 交付物）：它在 vendor 里是
#   **绝对像素**的高斯 σ、且作用在**整幅染料密度图**上（`grain.py:104-106`）⇒ 是"整幅柔度"。
#   `presets._scale_px_keys()` 渲染前乘 `实际长边 ÷ 本值`：交付尺寸（6264）因子 **1.0**（交付物逐位
#   不变）；2048 预览因子 0.327。
#   ⚠ 改成 `0`（关缩放）会让 2048 预览的 blur_eff 掉到 vendor `if sigma>0.4` 之下 ⇒ **预览一点柔化
#     都没有**；⚠ `0`/负 ⇒ 关掉这一根的缩放（与 `scanner.lens_blur` 同契约）。
GRAIN_BLUR_REF_LONG_SIDE = 6264

# ★★★ `scanner.lens_blur` 的**标定基准长边**（与 `grain.blur` 同一套机制）：vendor
#   `scanning.py:124` 直接 `apply_gaussian_blur(rgb, self._scanner.lens_blur)`、**无 µm 换算** ⇒
#   同一个 `0.32` 在两条路径上是**两个器件**（MTF @1000 线/像高：2048 ⇒ 0.618；6264 ⇒ 0.950）。
#   【单位】按 µm 读（交付口径 0.32 px × 5.7471 µm/px = **1.839 µm**，落在 f/5.6 衍射 1.34 µm 与
#   f/8 的 1.92 µm 之间）；实现上"µm"与"画面宽度比例"是同一个式子 ⇒ **不把 `film_format_mm`
#   写进公式**。⚠ 基准取 6264 ⇒ 交付因子 1.0；**2048 预览** σ_eff 0.1046 px ⇒ vendor
#   `radius = int(3σ+0.5) = 0` ⇒ 这一维**恒等**（不是 bug，采样栅格表示不了 1.84 µm 的 PSF）
#   ⇒ **必须在交付尺寸上判读**。
LENS_BLUR_REF_LONG_SIDE = 6264

# ★★★ halation 的**绿层核宽度 ÷ 红层核宽度**（物理常数，不是风格旋钮）：真实 halation 是
#   **近处橙、远处红**（绿层只在返光最强处被推动 ⇒ 一个**阈值非线性**）；而 `diffusion.py:72-75`
#   的 halation 段**三通道共用 σ** ⇒ 模型表达不出它。把 G 的核做窄 = 它的最佳线性替身；
#   G/R ∈ 0.4~0.5 ⇒ 取 **0.45**。
#   ⚠ `halation_strength` **一个字都不动**（它是引擎给 `('still','weak')` 的物理基线）。
#   ⚠ **能力边界**：紧贴边缘那一瞬间的比值仍 = a_R/a_G（归一化高斯的边缘响应峰值 = a/2、与 σ
#     无关）；本旋钮改的是"**多快变红**" —— "近橙远红"里**可线性表达的那一半**。
#   ⚠ 值**只在 config、钩子派生**（两处写值 = 两个真相）；`_HALATION_PRESETS` 那条路只在
#     `apply_stocks_specifics=True` 时跑到 ⇒ **在本项目里跑不到**。⚠ `0`/`≥1`/负 ⇒ 关掉它。
HALATION_G_SIGMA_RATIO = 0.45
# ★★★ **测光看人**（`subject`）：**只能改「看哪里」（测光方式，输入侧），不能改「看多少」
#   （曝光量，输出侧）** —— 后者是"读统计量 → 改全局参数"的闭环，**必然振荡**（Zone System 的
#   分工：曝光定暗部、显影/印相定高光，不许一个旋钮修两端）。
#   ⚠ **弃权是安全侧**：mediapipe 拿不到 / 没检出人 ⇒ 什么都不做，走原路径。
PERSON_METERING = True
#   ★★★ **它是否在岗取决于锚的 `w`**：`w = 1.0` 时 `ev` 被换成常数 ⇒ 本键成 **no-op**；
#     `w = 0.6` 时 `(1-w)·ev` **是逐图的** ⇒ 测光重新进入画面。
#   ⚠ 开 ⇒ `camera.auto_exposure_method='subject'`（`presets.py` 注入 person 掩膜）；关 / 掩膜不合理
#     （占比不在 [0.005,0.70]）⇒ 走引擎默认测光。**两种都仍然过锚、仍然逐图**。
#   ⚠ 它同时也是"要不要多跑一次 person 前向"的开销开关。

# ===========================================================================
# ===========================================================================
# **中灰锚**（`exposure.install()` 的 wrapper）
# ===========================================================================
# 【形式】`ev' = (1 - w)·ev + b` ⇒ 输入增益 k = 2**ev'（等价：归一化目标从**硬锚 `0.184`** 换成
#   `0.184 · (m/0.184)^w · 2^b`）。
# 【它修的病】`filming.auto_exposure()` 的 `ev` 分母是**写死的 `0.184`** ⇒ **场景的绝对亮度在测光
#   那一步就被丢掉**（实测 52 张：测光读数 SD 0.67 档，而颊 L* 对它的斜率是**负的**）。
# 【现值 = 「锚打底 + 人本」】`w = 0.6` / `b = 1.1181`（`b = 1.578 − (1-w)·ēv`，`ēv` = 参考卷全
#   52 张的测光读数均值 1.1498）⇒ 整卷平均落点与锚独裁时**逐档相同**，改的只是"性格"。
#   ⚠ **参考集必须用整卷**（逆光子集会偏亮、混合子集会偏暗）。
# 【陷阱】① **不许做成逐图 / 逐场景自适应**（那才会变成环）：`w/b` 是**链路常数** —— 卷 / 纸 /
#   扫描 / `γ_film` / 相机配方变了必须**重标**；② 它读**输入图** ⇒ 仍是 `输出 = F(输入)`，**没有环**；
#   ③ 改曝光会移动下游合成探针的工作点（增益 2.986 → 3.500）⇒ `selftest` 读数会漂（⚠ 出口白不受
#   影响）；④ 回退点 = `w = b = 0.0`。
# ──────────────────────────────────────────────────────────────────────────
EXPOSURE_ANCHOR_W = 0.6         # 锚打底 + 人本（测光拿回 40%）；1.0 = 锚独裁
EXPOSURE_ANCHOR_B = 1.1181      # 1.578 - 0.4x1.1498（整卷 52 张生产口径读数均值）
EXPOSURE_ANCHOR_W_FACE = 0.1186
EXPOSURE_ANCHOR_B_FACE = 0.0398





# ---- 出图缓存（只给常驻服务用）----
# ---- 出图缓存（只给常驻服务用）----
# ⚠ 默认关（`run_from(cache=None)`）⇒ 行为与不开缓存**逐位相同**；只有 service.py 打开。
# ⚠ 缓存键必须带：判据版本 / **引擎 overrides / 靶**（+ `CACHE_KEY_PREFIXES` 覆盖的那些旋钮）
#   —— 漏了后两项 ⇒ "按场景改参数"会命中旧缓存。
CACHE_ENABLE = True
CACHE_MAX_SETS = 4              # 最多留几套（一套 = 一张图 × 一卷 × 一组参数，约 16 MB）


# ==========================================================================
# ==========================================================================
# 预设模式（走 `presets.py` 直接渲染预设 JSON）；每份预设是**完整参数快照**（上百字段）。
# ⚠⚠ `PRESET_APPLY_STOCK_SPECIFICS` **不要改成 True**：引擎会按卷的「抗晕层」标签重写
#   `halation_strength` ⇒ 预设里调好的光晕被**静默**冲回出厂值（public GUI 里这开关**有状态**）。
PRESET_APPLY_STOCK_SPECIFICS = False
# ★★★ **配平基准 = 引擎数据库**（`neutral` 是"18% 灰在印相里完全中性"的起始设置），
#   引擎按 **(相纸, 放大机光源, 底片)** 三维取；`presets._apply` 里**恒开**、无硬编码回退。
#   ⚠ 读 DB 的顺序是 `c_filter, m_filter, y_filter`，**别按 y/m 猜**；换光源即查不到。
#   ⚠ 各预设的 `print_y/m_filter_shift` 是「风格偏移」，不是中性值本身。
# ★★★ `PRESET_FILTER_M_TRIM`（**印相品红滤片底座补偿**）：在标定基准之上统一多加一点品红**滤片剂量**
#   的旋钮，加在 `presets._apply`（`m_filter_shift += 本键`）⇒ **对预闪不生效**（预闪只读
#   `neutral + preflash_shift`）。
#   ⚠ 方向（10-18 实测）：`m_filter_shift` ↑ ⇒ 成片偏**绿**（滤片吸绿 ⇒ 相纸品红层曝光少），
#     不是"偏品红"；见引擎手册 §9.6。
#   ★ 为什么必须是 **0**：它是**一把全局的刀**（同一个 +8 会同时推动多条预设 —— 修好一条、弄坏两条）。
PRESET_FILTER_M_TRIM = 0.0
# ──────────────────────────────────────────────────────────────────────────
# 「印相滤片轴」：**零点在 `neutral`，`shift` 是逐预设/逐场景的偏移**。
#   · 主曝光 `filter_enlarger_source.py:17-18` ⇒ `m = m_neutral + m_shift`（y 同理）⇒ `neutral` 与
#     `shift` 是**同一根 CC 轴**（Kodak CC，`transmittance = 10^-(CC/100)`）。
#   · 预闪同文件 `:31-32` ⇒ 只读 `neutral + preflash_shift`（**不看 `shift`**）。
#   ⚠ **不许把预设的 `shift` 一起归零** —— 那是把预设的冷暖身份抹平。
PRESET_PE_SHIFT_FROM_SPEK = True    # 预设自带 pe 是否再乘 `SPEK_PE_SHIFT`
# 全部预设的落点平移系数。1.0 = 照用预设自带的印相曝光。
# ⚠ **只许 ±0.3EV 微调**；粗定位归引擎的 `print_exposure`。
SPEK_PE_SHIFT = 1.0

# ---- ★★ spektrafilm 的加速开关（vendor 出厂是关的，我们打开）----
# `use_enlarger_lut` / `use_scanner_lut`：印相/扫描不再逐像素展开 81 波段，改成 3 维 CMY 查表。
# `use_fast_stats`：统计量在缩略图上算。
# ★ 画面差比胶片自身的不可复现噪声还小（只开 LUT：平均 0.0004）；省一半时间。
# ⚠ 我们在 `spektra._apply_settings()` 里开，**vendor 文件一个字节都不动**。
SPEK_USE_LUT = True
SPEK_FAST_STATS = True

# ---- io.py ----

# ===========================================================================
# 输出
# ===========================================================================
JPEG_QUALITY = 95
