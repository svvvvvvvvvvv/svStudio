# 归档 · `svFilm/svFilm/data/_archive/`

> **来历**：`feature/clean-slate` 分支（从 `9e785c8` 拉出）上的「清账」。
> **用户原话**：「一堆的垃圾账 重新拉个分支吧 然后删掉各种旧的拍板旧的靶相关的代码
> 先留个原始框架和数值 再慢慢搞。这样比算这些旧账清晰一些」
>
> ★★ **本次做的是「移出去」而不是「删掉」**：每一个被搬走的键/注记都在这里**逐字**保留。
> **备份分支 `feature/drop-grade` 一个字节都没动**（那是唯一备份点）。
>
> ★★★ **硬约束（已逐批验证）**：**数值零变化**。
> 证据 = 参数级全字段 SHA256 指纹（`fp_raw` / `fp_core` / `fp_eff` / `ov`）+ `selftest` 116 ok / 1 红。

---

## 1. 目录结构

| 路径 | 是什么 |
|---|---|
| `keys_moved.json` | **索引**：每批动了哪些文件、搬了多少项 |
| `pre_clean_slate/` | ★★ **清账前（`9e785c8`）的 11 份主文件原样** —— **真正的"改动前"**，可整份拷回 |
| `B1_dead_keys_unread.json` | B1 的**逐字**原文（`presets.py` 全文件都不提的死键） |
| `B2_backup_files.json` | B2 的清单（两个不在加载路径上的备份文件） |
| `B3_dead_targets.json` | B3 的**逐字**原文（死靶 + `_provenance` 死条目 + 旧轴注记） |
| `backup_files/` | 从 `presets/` 搬出来的两个备份（原样保留，见 §3） |
| `snapshots/` | *（已清空）* —— 见下面的 ⚠ |
| `README.md` | 本文件 |

> ⚠ **一处自己的 bug（已修，留证）**：第一版 `_apply_cleanup.py` 把
> `snapshot_batch()` 写在 `BATCHES[n](True)` **之后** ⇒ `snapshots/B1` / `snapshots/B3`
> 存的其实是"**改完**的"文件，**等于没用**。
> **修法**：从 `git show 9e785c8:<file>` 取 11 份主文件写进 **`pre_clean_slate/`**
> （逐字节 = 清账前的 HEAD 版），并删掉那两个错的目录。
> ★ 教训：**快照必须在写之前拍** —— "改动后拍快照"看起来一模一样，但它什么也证明不了。
> （发现方式：复核时用"快照里应含/不含某个已搬走的键"去反向验，B1 的快照里居然还有
> `particle_scale_layers` ⇒ 当场露馅。）

## 2. 三批各搬了什么（每批一个 commit，可单独 `git revert`）

### B1 `4d54caf` — 预设 JSON 里「`presets.py` 全文件都不提」的死键（10 条 × 130 项）

**判据（比"没读点"更强）**：这一类键 `presets.py` **一个字都没提**
⇒ 从 JSON 删掉，`_apply()` 连 `KeyError` 都不会有。
（判据脚本：`E:\ds\photo\清账_1012\_probe5.py`）

| 键 | 为什么它死了 |
|---|---|
| `special.print_gamma_factor` | 0.3.4 的 `PrintRenderingParams` **没有该字段**；`presets.py` 里那行赋值**已于 10-10 删除**（见 `presets.py:384-395` 自带的长注记）⇒ 全 10 条的 0.93~1.07 **从未生效** |
| `load_raw` 整段（`white_balance` / `temperature` / `tint` / `lens_correction` + 它自己的 `_note_load_raw`） | 预设注记**原文**：「**这一段在本链上是死键，一个字节都不读**」；而且 10 条**全是同一组值**（custom / 5200 / 1.0 / false）⇒ **连信息量都没有** |
| `display.gray_18_canvas` / `output_interpolation` / `use_display_transform` / `white_padding` | `_apply` 只读 `display.preview_max_size`（**保留**），这 4 个提都不提 |
| `simulation.auto_preview` / `saving_color_space` / `saving_cctf_encoding` | GUI 存盘/预览开关，`_apply` 提都不提（`output_color_space` 是**活键、保留**） |

⚠ **刻意不搬**（见报告 §5）：`grain.particle_scale_layers` / `grain.micro_structure` /
`grain.blur_dye_clouds_um` —— 引擎不读（`grain.sublayers_active=False`），但
**`presets.py:492 / 496 / 497` 会取** ⇒ 要搬必须先改那三行代码（`gr['x']` → `gr.get('x')`）。
**那属于改代码，本次不做**（用户要求"引擎框架原样"）。

### B2 `024dfe9` — 两个不在加载路径上的备份文件

| 搬走的 | 来历 | 为什么它不是现役 |
|---|---|---|
| `presets/_wgate_backup/`（7 个 JSON） | 10-11 加白端门控注记时 `add_notes.py` 的自动备份（**已被 `.gitignore` 忽略、从未跟踪**） | 实测这 7 个**都不含** `_note_white_gate` ⇒ 是"加注记**之前**"的中间态残骸 |
| `presets/Ultramax400沉褐.json.bak_paper` | `3068699`（10-10 换相纸）留下的**换纸前**快照（**被 git 跟踪**） | 与现役差 4 处：`print_paper` / `m_filter` −0.95→−1.05 / `y_filter` 5.253→3.0 / `scan_black_level` 0.01→0.03 ⇒ **它是唯一记录着换纸前那一版的文件** |

**为什么可以搬**：两者都**不在** `presets._files()` 的加载范围
（`_SUFFIX='.json'` + **非递归** `os.listdir`）⇒ 零画面影响。
复核：`presets.names()` 仍是 **10 条**。

### B3 `9233984` — `targets.json` 的死靶 + 「描述已删轴 / 已撤决定」的旧注记（84 项）

**(a) 死靶值（逐键证明零读点）**

| 搬走的 | 为什么零读点 |
|---|---|
| `_default` 与三条大师预设各自的 `L5` / `L50` / `L95` / `C50` | `targets.for_stock()` 会把它们带出来，但**全仓唯一消费者** `scorecard.targets_of()` **只取 `span` 一个键**（`scorecard.py:183-185`，本次现场复核：`targets_of('Ultramax400沉褐') == {'span': (81.65, ...)}`）。`config.py:89-91` 原文也写着那批字段「本轮先留着……**下一轮再清**」 |
| `_provenance` 里除 `span` / `n` 以外的 **21 条** | `scorecard.scope_of()` 只被 `scorecard.main()` 对**打分项**调用，而打分项**只有 span**。其中 `black_shape` / `hi_shape` / `sh` / `hi` **自己就写着「★ 已作废」**；`band_gain` 的 `use` 写的「依据 = 画面内关系量」在颜色层 10-10 删除后**没有任何消费者**（该键在交付链上零读点） |

**(b) 旧注记（描述【已经不在文件里】的轴，或【已经撤回】的决定）**

| 搬走的 | 它描述的东西现在在哪 |
|---|---|
| `_scene_engine._note_back_span_1008` | `back=` 轴 —— **10-09 `_note_no_back_tone_1009` 已删** |
| `_scene_engine._note_span_flat_1008` | `span=平` 轴 —— **10-12 `9e785c8` 已删**（负向碑 `_note_span_flat_1012_removed` **保留**、且其中引用了它） |
| `_note_back_restore_1010` · `_note_lighttype_migration_1010` · `_note_lighttype_action_1010` · `_note_exposure_revert_1011` | `back=正逆光` → `lighttype=逆光` → 撤除 的**过程记录**；那两块**现在都不在文件里**，结论已由保留的 `_note_no_back_tone_1009` / `_note_no_lighttype_tone_1011` 承担。⚠ `_note_lighttype_action_1010` 里的**用户 10-11 目检原话**（「都可以再亮些 然后压下暗部」）在 `_note_tone_allow_1011` 里另有一份同样的引文 |

**为什么搬走不改画面**：`_scene_engine` 的轴层循环**只按 `scene.AXES` 查键**
（`exp` / `span` / `back` / `overwhite`）+ 一档 `lighttype=`，
**以 `_` 开头的键永远不会被 `ov.get()` 命中** ⇒ 注记是惰性数据。
证据：指纹 `ov:<4 场景>` 与 `fp_eff:<4 场景>` 逐条不变。

### ★ 全部**保留**（它们是当前纪律与负向碑，不是旧账）

`_tone_allow`（= `{}`）· `_note_tone_allow_1011` · `_note_no_lighttype_tone_1011` ·
`_note_tutorial_1011` · `_note_lightness_compression_1011` · `_note_grain_1009` ·
`_note_revert_1009` · `_note_print_exposure_1012` · `_note_span_flat_1012_removed` ·
三段 `_stock_engine._note_*` · 各预设全部 `_note_*`（含所有"只加注记、一个键值没改"的碑）。

---

## 3. 时间线（为什么会有这些账 · 一眼看懂"旧账怎么堆起来的"）

```
09-29  颜色层口径修正 + 影调层/肤色层/认人 整段删除（da 分支 drop-tone-and-skin）
10-08  配平基准换源（硬编码常数 -> 引擎 DB 三元组查表）⇒ 各预设 shift 重表
       ★ 副作用：`PRESET_FILTER_M_TRIM = +8 CC` 那把全局刀归零时，
         各预设的 shift 保的是"含 trim 的观感" ⇒ 刀拔了、刀口留在肉里（4 条至今带 +8）
10-09  back= 影调参数删 · grain.blur 定标到 3.0 · 颜色层撤回三项
10-10  ★ 颜色层 grade.py **整段删除** ⇒ 靶层（band_gain/sh_abs/hi_abs/...）**集体变死**
       换相纸（Ultramax -> 富士水晶）· 恢复 back=正逆光 · `_provenance` 立"只有同内容对照"
10-11  back= -> lighttype= -> 整块撤除 · 中灰锚 w=1.0/b=1.578 落地（**未经拍板**）
       白端门控注记（_wgate_backup/ 就是它的自动备份）
10-12  曝光主人改定 w=0.6/b=1.1181（用户目检拍板）· 启用 _stock_engine（4 键下沉）
       lc 逐预设分家（增田/滨田 0.85）· 删 span=平 · lc 0.90 试完撤回
10-12夜 ★ 清账（本归档）：B1 死键 / B2 备份文件 / B3 死靶与旧注记
```

## 4. 回滚

```powershell
cd E:\svStudio
git log --oneline -6                     # 4d54caf / 024dfe9 / 9233984 / 8a6da90 / …
git revert <commit>                      # 任意一批单独回滚
# 或整份拷回（★ 真正的"清账前"）：
Copy-Item -Force svFilm\svFilm\data\_archive\pre_clean_slate\targets.json svFilm\svFilm\data\
Copy-Item -Force svFilm\svFilm\data\_archive\pre_clean_slate\*.json      svFilm\svFilm\data\presets\
```
★ 更彻底的备份 = **分支 `feature/drop-grade`**（清账前的原样）。


---

## 第二节 · B7（第二阶段：**注记精简**，10-12）

> **用户原话**：「**注意也需要删掉那些历程 各种啰嗦的地方 只留下功能性注释 尽量精简代码**」，
> 追问后追加：「**别点头 直接给我删掉**」。
>
> **性质**：**零渲染** · **一个生效键都没动** · 每批 `selftest` 都是 **116 ok / 1 红**（基线该红）。

### B7 各批（每批一个 commit，可单独 `git revert`）

| 批 | commit | 动了什么 | 数值证据 |
|---|---|---|---|
| **N1** | `8a7da21` | ★ **3 个 grain 死键搬走** + `presets.py` 那三行加 `if ... in gr` 守卫 | 全字段快照 84730 个字段里**只有 10 个变**（每条预设 1 个）：`grain.particle_scale_layers` [2.5,1,0.5] -> [2.0,1,0.5]（**schema 默认**）⇒ 该字段只在 `sublayers_active=True` 时被读（`grain.py:179`），我们 10 条全是 False。另两个键（`blur_dye_clouds_um` 1.0 / `micro_structure` (0.2,30)）**与 schema 默认逐位相同 ⇒ 连字段都没变**。`ov:*` 指纹 **4/4 SAME** |
| **N2** | `d48a3eb` | `targets.json` 15 条注记精简 | 全字段 **0 变化** |
| **N3** | `9db2489` | `Ultramax400沉褐` 26 条注记精简 | 全字段 **0 变化** |
| **N4** | `125b714` | `GOLD200暖橙` + `Pro400H清风` 18 条 | 全字段 **0 变化** |
| **N5** | `b196eee` | 7 条预设 61 条 | 全字段 **0 变化** |
| **N6** | `980da0d` | `config.py` 7 段长注释 13083 -> 6251 字 | 全字段 **0 变化**；`git diff` 逐行核过**非注释行 0 处** |

### 归档里有什么

* **`B7_note_history.json`** —— ★ **每一条被精简/移走的注记的逐字原文**（键 = 文件名；值 = {注记路径: 原文}）。
  还原方式：把原文贴回同名键即可（顶层 `_archive` 指针不必还原）。
* **`B7_dead_keys_grain.json`** —— 3 个 grain 死键 × 10 条预设的逐字值 + "为什么以前搬不动/为什么现在能搬"。
* **快照**：本批不必另拍 —— 改动前 == `a09e332`（工作树干净），`git show a09e332:<file>` 即逐字节原文。
