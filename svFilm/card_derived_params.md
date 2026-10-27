# 不要预设：卡片的成片特征 → 动态算全部参数

> 纠正：刚才的 A/B 用的是旧「鹿井预设」，那是错的方向。  
> 目标不是“先有一个预设，再从卡修一点”，而是：**匹配到目录后，用这个目录里的成片特征，动态算出全部参数**——卷、纸、颗粒、光晕、化学、PE、WB、amount、gamma、黑白端，以及 LR 的 Basic / 曲线 / 分级 / HSL。  
> **没有预设。卡片就是唯一的风格源。**

---

## 1. 卡片给什么

匹配到目录（比如 38 = 104_鹿井小渔）后，读这个目录里的**成片**（16 张），量：

```
skin:  C 的 p25/p50/p75, L 的 p25/p50/p75, h, dC, C_rank
tone:  p5/p25/p50/p75/p95, contrast, black5, white95
grade: shadow a/b, mid a/b, high a/b
cloth: L/C      bg: L/C
texture: grain rms/blur/dye, halation, scan softness
comp: face%, env%, light_ratio
tags: light.source, clothing.palette, scene.place, background.type
```

不是取一张，是取这个目录的**分布 + 关系 + 秩**。  
卡 38 的现成数：`skin C 17.2 / L 77.1 / h 48.7`，`p50 56.2`，`contrast 82.3`，`black5 1.36`，`white95 0.66`。

---

## 2. 卷/纸：从卡的特征里选/拟合，不是固定 3 个

### 2.1 候选表（anchors，不是生产）

现有 3 个 profile 只当**候选锚点**：

| anchor | 卷 | 纸 | 已知签名 |
|---|---|---|---|
| A | Portra 400 | Endura | 肤色稳、青/橙平衡、颗粒中 |
| B | Gold 200 | Endura | 暖黄、颗粒更粗、阴影偏暖 |
| C | Portra 400 | Endura | 6×7 长边、景深/柔度不同 |

**它们不是“选一个套上去”，是给求解器一个可插值的起点。**

### 2.2 选/拟合规则

1. 从卡的成片量**介质签名**：颗粒 rms/blur/dye、halation、shadow/high a/b、皮肤 h/C、黑白端、format。
2. 与候选锚点的**签名距离**排序：最近的 1–2 个作为初值。
3. 在引擎允许的量程内，**连续拟合**：
   - `grain.rms_granularity`、`grain.blur_dye_clouds_um`、`halation.*`、`scan_unsharp_mask`、`black_level/white_level`；
   - 卷/纸的离散键只做**初值**，数值强度全部由卡的特征解出来。
4. 如果引擎的 stock/paper 键必须离散：选最近锚点，但**所有数值参数仍按卡解**；离散键只影响卷表/纸表的形状。
5. **没有“鹿井预设”这回事**；卡 38 的卷/纸不是“因为它是鹿井所以 portra”，而是“它的成片签名最接近 A，且数值按它解”。

---

## 3. 引擎参数：全部由卡解

| 参数 | 从卡的什么来 | 怎么解 |
|---|---|---|
| `print_exposure` / 动态 PE | 卡的 `L` 分布 / 脸 L | 目标 L 与当前 L 的差 → 引擎 PE 敏感度 |
| `wb` | 卡的中性 a/b + 皮肤 h | 同图中性差 → WB 敏感度；皮肤 h 约束 |
| `couplers.amount` | 卡的 `C` / `C_rank` | 目标 C 与当前 C 的差 → amount 敏感度 |
| `film_chemistry.gamma` | 卡的 shadow/high a/b、皮肤 h | 红黄配比 → gamma 敏感度 |
| `scanner.black/white` | 卡的 `black5/white95` | 端点差 → 敏感度 |
| `grain.*` | 卡的颗粒签名 | 直接拟合 rms/blur/dye |
| `halation.*` | 卡的高光边/光晕 | 直接拟合强度/半径 |
| `scan_unsharp_mask` | 卡的清晰度/光学柔 | 拟合 σ/amount |
| `film_format` | 卡的画幅/景深 | 选长边；影响 σ_px |

**没有“因为预设是鹿井，所以 amount=1.00”**；amount 是解出来的。

---

## 4. LR 参数：同一个卡目标，另一个 solver

- **Basic**：WB/Exposure/Contrast/Highlights/Shadows/Whites/Blacks —— 从卡的 `L`、`p25/p75`、`p5/p95`、`black5/white95` 解。
- **Tone Curve**：卡的分位锚点（p5/p25/p50/p75/p95）→ 单调曲线；不是 CDF。
- **Color Grading**：卡的 `shadow/mid/high a/b` → 三段 hue/sat/lum。
- **HSL**：卡的皮肤 h/C、背景 h/C → 橙/黄、绿/蓝局部。
- **顺序**：WB → Exposure → Basic 粗 → Curve → CG → HSL；一个特征一个 owner。

**LR 的每个滑杆值也都是解出来的，不是预设里抄的。**

---

## 5. 求解器长什么样

```
输入：卡的 target（分布/关系/秩）+ 当前批次的 baseline + 介质锚点
1. 介质：签名距离 → 锚点初值 → 连续拟合 grain/halation/scan/黑白端
2. engine：PE/WB/amount/gamma 解到卡 target
3. LR：Basic → Curve → CG → HSL 解到同一 target
4. 量残差；2–3 轮；每轮只动“残差最大且 owner 对应”的旋钮
5. 同批同参数；outliers 只标不拉
```

- 敏感度：engine 探针 + LR 的 `cal_basic/cal_curve_jac/cal_grading/cal_hsl`。
- 损失：分位/关系/秩加权；不用全图 CDF；不用绝对 C 当“浓不浓”。
- 约束：脸/皮肤硬门；背景可过曝；单调曲线；量程。

---

## 6. 刚才的 A/B 错在哪，接下来怎么验

- 刚才：`鹿井预设` 渲 919 → 对 104。**错在“预设”**；它只是旧 master 的模样，不是卡 38 的特征。
- 正确：**先从 104 的 16 张成片解出参数**（卷/纸/颗粒/光晕/PE/WB/amount/gamma/黑白 + LR Basic/Curve/CG/HSL），再用这组参数渲 919 的 3 张，对 104。
- 第一版可以只做**第一轮**（介质锚点 + PE/WB/amount/黑白 + LR 的 Exposure/Curve/CG/HSL 粗解），看能不能把刚才那个“柔/粉/低对比”拉到 104 的“青/橙/重”。
- 不渲整批；只 3 张；A/B 私有。

---

## 7. 防坑

- ❌ 先有预设再从卡修一点。
- ❌ 卷/纸固定 3 个、不按卡签名。
- ❌ amount/PE/曲线从预设抄。
- ❌ 用“鹿井/滨田/xhs”当风格，而不是卡。
- ❌ 用绝对 C、全局中位数、CDF。
- ❌ 逐图调；批次级。
- ❌ 换卷/纸不重跑 base/J。
- ❌ 拿旧 master targets 当卡 target。

---

## 8. 下一步（不渲整批）

1. 写 `card_target`：38 的 16 张 → 分布/关系/签名。
2. 写介质锚点 + 签名距离 + 连续拟合（先 grain/halation/scan/黑白端）。
3. 写 engine 的 PE/WB/amount/gamma 解。
4. 写 LR 的 Basic/Curve/CG/HSL 解。
5. 用 38 的第一轮参数渲 3 张 919，A/B；不整批。
