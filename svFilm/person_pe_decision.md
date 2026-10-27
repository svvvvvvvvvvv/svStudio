# 要不要把「以人为本」的曝光机制加回来

> 分支：`feat/person-pe`（从 `feat/lrfinish` 的 `b980677` 拉出）  
> 结论（2026-10-27 更新）：**暂缓，不启用「以人为本」这套 person PE**；当前分支只做清理。  
> 旧的 `render_person_pe` 留在历史分支当 reference，不回到主链。

---

## 1. 刚刚提交了什么

- `feat/lrfinish` 上提交 `b980677`：`chore(svFilm): snapshot lrfinish v9 state before person-PE experiment`
  - 10 个文件：`lrfinish/opt/*`、`profile_io.py`、`test_v9.py`、`鹿井预设/finish.json`、`preset.json`
- 新分支：`feat/person-pe`
- 未提交（留在本地）：候选预设目录（Arm/Best/Fix/大胆/残差J/生产/直方图/策略J/策略S1）、`_smoke_919/`、两个 list、`preset.json.bak_1024`
  - 这些是实验/烟测/备份，不是当前 lrfinish 的代码；要收我再单独处理。

---

## 2. 旧机制是什么

`00d6265` 到 `9929dd7` 这一串，是「以人为本的印相曝光」：

- `render_person_pe()` / `person.py`：人物分割 + 脸/服装区域
- `person_pe_band`：脸 L* 接受窗 [P10,P90]；窗内不动，低于下沿提向 P25，高于上沿压向 P75
- `person_pe_white_band`：亮中性服装 L50 带，只截「往亮提」
- `person_pe_cloth_p99_max` / `person_pe_cloth_clip_max`：全色服装的 p99/clip 天花板
- `person_pe_tail` / `person_pe_mid`：尾巴目标与中点（10-19 重定）
- `person_pe_lc: "auto"`：脸已在带内时，用输出明度压缩压亮白服，再渲一遍（A 方案，默认 off）
- 两遍印相：第一遍 PE 到脸目标，第二遍 lc / 服装；光位 → 力度

它的本质不是“曝光补偿”，是**按人物区域分配印相曝光**：脸先落进带，服装/背景有天花板，背景过曝可以接受。

---

## 3. 为什么被删

`ff194eb`（2026-10-21）写得很明确：

```
sfFilm owns the medium（卷/纸/扫描形状、颗粒、光晕、扩散、基色）
Lightroom owns placement and scale（曝光、彩度、暖度、肩/趾强度）
```

删掉的是「用形状旋钮追 placement 目标」的整层：

- `person.py`、`face_roi.py`、`scene.py`、`targets.py`、`scorecard.py`
- `presets.py` 的 `render_person_pe / _person_metering / _lc_* / render_metered / render_linear`（~840 行）
- `pipeline.py` 的 person_pe / targets 分支

原因不是“效果不好”，是**职责冲突**：engine 既做介质又做 placement，LR 又做 placement，两边互相顶；而且 person_pe 是**逐图**的，跟“布光/批次一致”天然打架。

---

## 4. 新架构下它该放哪

现在的新链路是：

```
匹配 → 目录卡（+子卡）→ 卡片 target（分布）
介质 profile = base
引擎 / LR = 两个 solver
```

- **卡片 target 里有脸 L**（`skin L p25/p50/p75`、`ΔC/C_rank`、`p50/contrast`、`shadow/high`）。
- **介质 profile** 只给卷/纸/颗粒/光晕/格式，不给 placement。
- **placement（曝光/彩度/暖度/肩趾）在 LR**，这是 `ff194eb` 定下的分工。

所以「以人为本」的落点逻辑**不应该回到 engine 里当 `print_exposure` 的第二遍**，否则又把 placement 塞回介质层，跟 LR 打架。

**它应该变成 placement 层的一个可选 solver：**

- 输入：卡片的脸 L target、服装 ceiling、背景允许过曝；
- 作用：在 LR 的 Exposure/曲线/蒙版上，按人物区域做**有界的局部落点**；
- 或者：engine 出 medium-only，后面接一段**独立的 placement pass**（不是 engine 内部的两遍印相）；
- 批次级：同一批用同一组 face target / ceiling，不逐图手调；
- 有界：脸在带内不动；提/压有上限；服装 ceiling；背景可过曝；
- 可选：默认 off，A/B 过再开。

---

## 5. 四个选项

| 选项 | 做法 | 代价 | 判断 |
|---|---|---|---|
| A 什么都不加 | 当前 lrfinish + LR per-image EV/curve | 脸 L 靠 LR 手动/自动；批次一致性靠人 | 最干净，但“以人为本”的经验丢了 |
| B 原样加回 engine | 恢复 `render_person_pe` / `person.py` / 两遍印相 | 与 LR placement 冲突；逐图；~840 行回归；ff194eb 白做 | ❌ 不推荐 |
| C 移到 placement 层 | 脸/服装落点做成 LR 蒙版/EV 或 engine 后的独立 pass，卡片 target 驱动，批次级、可选 | 要写新 solver；要和 LR 的 EV/曲线协调 | ✅ 推荐 |
| D 混合 | engine 保持 medium-only；LR 做 placement；旧 person_pe 只当 reference/对照 | 两套 placement 要 A/B | 可作为 C 的验证路径 |

---

## 6. 推荐：C，不是 B

**保留「以人为本」的思想，不保留它的位置。**

- engine 继续 medium-only（`ff194eb` 的分工）。
- `feat/person-pe` 上做 **placement solver**：
  - 从卡片 target 取脸 L 带、服装 ceiling、背景允许；
  - 在 LR 里用**人物蒙版 + Exposure/曲线**落点，或在 engine 后接独立 pass；
  - 批次级参数，不逐图；
  - 默认 off，A/B 过再开。
- 旧 `render_person_pe` 只作为**对照实现**（reference），不直接进主链。
- 这样既保住“脸先落进带、服装有天花板、背景可过曝”的经验，又不破坏“engine=介质、LR=placement”的分工。

---

## 7. A/B 的判据

不开“感觉像不像”，看：

1. **脸 L 残差**：批次脸 L 到卡片 `L_p25/p50/p75` 的距离；C 用 `ΔC/C_rank`，不用绝对 C。
2. **批次一致性**：同批脸 L 的方差；比逐图 LR 更稳才算赢。
3. **服装/背景**：服装 p99/clip 不超 ceiling；背景过曝允许，但脸不飞。
4. **两路一致**：engine medium + placement vs 纯 LR；特征残差谁小。
5. **成本**：person 分割/脸检测的失败率；face_small/遮脸要能弃权。
6. **可复现**：同参两渲差；placement 不能引入随机。

---

## 8. 下一步（不渲染 919）

1. 这篇就是决策文档；你拍板 A / C / D。
2. 若 C：先在 `feat/person-pe` 写 **placement solver 契约**（输入卡片 target、输出 LR 蒙版/EV 或独立 pass 参数），不接 919。
3. 用 **探针/合成图** 做 A/B：旧 `render_person_pe` vs 新 placement vs 纯 LR；看脸 L 残差/批次方差/服装 ceiling。
4. 过了再谈 919 的渲染；现在不动原图。

> 一句话：**「以人为本」要的是脸/服装的局部落点，不是 engine 里的第二遍印相。**  
> 把它从 engine 挪到 placement，才和新架构不打架。
