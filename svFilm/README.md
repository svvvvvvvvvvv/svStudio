# svFilm

数码仿胶片引擎（重写版）。**一条链**：
**RAW/JPG → 解码 + 白平衡 + 护栏 → 判场景 → spektrafilm（物理胶片链）→ 出图**。

> 本目录是 `<svStudio 仓库>/svFilm/`（git subtree 合进来的）⇒ 拉 svStudio 一个仓库就够。
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
| **CLI** | `python -m svFilm.cli one <图> --stock 鹿井预设` | 脚本 / 一次性任务 |
| **本地 HTTP 服务** | `python -m svFilm.service`（**常驻**，只绑 `127.0.0.1:8765`） | 工作台 svStudio |

★ **两条口子是同一个引擎**（CLI 是薄壳）—— 不许各写一套算法。
★ ⚠ **绝不允许「每次调用起一个进程」**：`io.load_raw` 一个人占全链一半时间，
  常驻 + 解码缓存是硬要求（冷启动 5.0 s vs 常驻 1.45 s）。

## 跑起来之前

```bash
# 只装这些 —— 胶片引擎 spektrafilm 已经随仓库带在 _tools/spektrafilm/，不用自己装
pip install numpy Pillow opencv-python rawpy colour-science mediapipe
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
```

## 常用命令

```bash
python -m svFilm.cli one   <图> --stock 鹿井预设    # 单张
python -m svFilm.cli dir   <目录> --jobs 4          # 批处理（重活建议 <=4：内存）
python -m svFilm.cli probe <图>                     # 只分析、不写文件
python -m svFilm.cli stocks                         # 列出可用预设
python -m svFilm.cli calib <RAW+JPG 同名对目录>       # 量机型表要填的基线增益

python -m svFilm.service                            # 常驻服务（127.0.0.1:8765）
python -m svFilm.scorecard <目录>                    # 评分卡
python -m svFilm.hist <图>                          # 直方图
python -m svFilm.selftest                           # 自检（动完任何一层都要跑）
```

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
