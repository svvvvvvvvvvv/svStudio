# 匹配之后：LR Basic / 色调曲线 / 颜色分级 怎么算

> 前提：**大师不再是预设**。卡片 = 目标（分布 + 关系 + 情境）；介质 profile = 底；  
> **engine 管介质，LR 管 placement + look**（`ff194eb` 的分工）。  
> 这里只写“怎么算”，不写“参数长什么样”；参数由卡片目标解出来。

---

## 0. 一句话

**LR Basic = 落点（WB / 曝光 / 黑白端 / 全局对比）**  
**Tone Curve = 分布形状（toe / shoulder / 中间调）**  
**Color Grading = 阴影/高光的染色方向**  
**HSL = 肤色/环境色的局部修正**  

四条**各管一段**，不许互相顶；engine 只出介质，不抢 placement。

---

## 1. 先定“谁管什么”（防双算）

| 目标 | 唯一 owner | 不许拿它管 |
|---|---|---|
| 白平衡/中性 | **LR WB**（raw 场景）+ engine 介质 WB | 不许用 Color Grading 改整体白平衡 |
| 整体亮/脸落点 | **LR Exposure** | 不许用 Contrast / 曲线补 |
| 全局明暗分布 | **LR Basic Contrast**（粗）+ **Tone Curve**（形状） | 两者只做一次，Basic 先、曲线后 |
| 黑/白端 | **LR Whites/Blacks**（粗）+ Tone Curve 端点 | 不许用 Exposure 压 |
| 高光滚降/阴影 | **LR Highlights/Shadows**（粗） | 不许用 Color Grading 的 Lum 当曝光 |
| 肤色 | **HSL 橙/黄**（局部） | 不许用 WB 改肤色；不许用 Saturation 全局拉 |
| 环境色/背景 | **HSL 绿/蓝**（局部）+ Color Grading | 不许用全局 Saturation 一刀切 |
| 阴影/高光染色 | **Color Grading** 三段 | 不许用 Split Toning 另开一套 |
| 颗粒/扫描/光晕 | **engine / 介质 profile** | 不许用 LR Grain/Clarity 补 |
| 镜头/透视 | LR Lens / Transform | 不许用 Dehaze 当对比 |

> **一条硬规则：同一个特征只能有一个 owner。** 如果两个旋钮都能动它，就用敏感度矩阵选“单位代价最小”的那个，另一个锁死。

---

## 2. 目标向量：卡片给什么

每张卡已有（`directories.json` / `images.jsonl`）：

```
skin:  C p25/p50/p75, L p25/p50/p75, h, dC, C_rank
tone:  p50, contrast, black5, white95, p25, p75
grade: shadow.a/b, high.a/b, mid.a/b, warm_cool
cloth: L/C     bg: L/C
comp:  face%, env%, light_ratio
tags:  light.source, clothing.palette, scene.place, background.type
```

**不给单值，给分布 + 关系：**

- 肤色：`C_rank`、`dC`（相对同图中性）、`L_face`；**绝对 C 只参考**。
- 明暗：`p25/p50/p75`、`p5/p95`；**不用全局中位数当靶**。
- 颜色：`shadow.a/b`、`high.a/b` 的关系；**不用“暖/冷”标签当 target**。
- 情境：匹配已保证批次与卡片情境相近；卡片 target 是**风格**，批次光是**情境**，不混。

---

## 3. LR Basic 怎么算

### 3.1 WB（Temp / Tint）

- 目标：卡片的**同图中性** `(a_n, b_n)` + 肤色 `h_s`；不是卡片的整体黄蓝。
- 当前：批次的 `(a_n0, b_n0)`（同图中性，用 FACE/低 C 像素；不是灰卡）。
- 增量：`Δa = a_n - a_n0`，`Δb = b_n - b_n0`。
- 解：`[ΔTemp, ΔTint] = J_WB^{-1} · [Δa, Δb]`，并对 `h_s` 做小权重约束（肤色不能为了中性被拉歪）。
- 约束：`|ΔTemp| ≤ 1500K`、`|ΔTint| ≤ 20`；**不追绝对色温**。

### 3.2 Exposure（EV）

- 目标：卡片的 `L_face`（p25/p50/p75）**优先**，`L_p50` 次之。
- 当前：批次的 `L_face0`（同脸/颊；不是脸框）。
- `EV = clamp(log2(Y(L_face_target) / Y(L_face0)), -1.5, +1.5)`，`Y(L)` = 线性亮度。
- 若 `L_face0` 不可信（face_small/遮脸/过曝）：退到 `L_p50`，置信降一档。
- 高光守卫：`p99` 不超 `white95 + 2`；背景过曝允许，脸/皮肤不许飞。
- **批次级**：同批一个 EV；outliers 单独标，不逐图。

### 3.3 Contrast（Basic）

- 目标：卡片的 `p25/p75` 与 `contrast`；当前：批次同量。
- 粗解：`ΔContrast = k_c · ( (p75_T - p25_T) - (p75_0 - p25_0) )`，`k_c` 由 LR 滑杆探针标定。
- **只做粗**；剩下的 toe/shoulder 交给 Tone Curve。两者不重复。

### 3.4 Highlights / Shadows（Basic）

- 目标：卡片 `p95`（高光）与 `p5`（阴影）；当前：批次同量。
- 粗解：`ΔHighlights = k_h · (p95_T - p95_0)`；`ΔShadows = k_s · (p5_T - p5_0)`。
- 约束：`|Δ| ≤ 40`；脸的高光优先（脸不飞）；背景可过。

### 3.5 Whites / Blacks（Basic）

- 目标：卡片 `white95 / black5`；当前：批次同量。
- 粗解：`ΔWhites = k_w · (white95_T - white95_0)`；`ΔBlacks = k_b · (black5_T - black5_0)`。
- **端点**：曲线端点只做微调；Basic 是粗，Curve 是细。

### 3.6 Texture / Clarity / Dehaze

- **不归 LR Basic**：颗粒/扫描/光晕归 engine/介质；Clarity/Dehaze 不拿来补对比。
- 若卡片有明确“光学柔/锐”差异：用介质 profile 的 `scan_unsharp_mask`/`lens_blur`，不在 LR 里补。

### 3.7 Vibrance / Saturation

- **不单独当肤色 owner**：肤色走 HSL 橙/黄；环境走 HSL 绿/蓝；整体 C 的粗调可由 `Vibrance` 小步，但**必须和 engine `couplers.amount` 分工**。
- 规则：engine 的 `amount` 是介质/彩度分配；LR 的 `Vibrance` 只做局部/场景修正；两者不同时拉满。

### 3.8 Basic 的顺序

```
WB → Exposure → Contrast（粗）→ Highlights/Shadows（粗）→ Whites/Blacks（粗）
→ Tone Curve（形状）→ Color Grading（染色）→ HSL（肤色/环境）
```

每步之后量一次特征；后一步不许把前一步的目标拉回去（用约束/惩罚）。

---

## 4. Tone Curve 怎么算

### 4.1 输入/输出

- 输入：批次在当前 Basic 下的**亮度分位** `x = [p5, p25, p50, p75, p95]`（归一化到 0–1）。
- 目标：卡片同分位 `y = [p5_T, p25_T, p50_T, p75_T, p95_T]`。
- 输出：一条**单调**曲线 `f: x → y`，用 **PCHIP / 单调三次** 过 5 个锚点；端点 `(0,0)`、`(1,1)` 可微调但不许反折。

### 4.2 不做什么

- **不做直方图/CDF 匹配**：不拿全图 CDF 硬套；只用 5 个分位锚点。
- **不逐图**：同批一条曲线；outliers 不单独拉。
- **不做 per-channel 曲线**：RGB 合成一条；颜色交给 Color Grading/HSL。
- **不和 Basic 抢**：Basic 先定 `p25/p75` 的粗对比，曲线只修**形状**（toe/shoulder/中间调）；如果 Basic 已经把 `p25/p75` 打到位，曲线在锚点处应接近 `y=x`。

### 4.3 约束

- 单调、`f(0)=0`、`f(1)=1`、`|f'(x)| ≤ 3`；
- 脸的 `L` 落在卡片的 `L_face` 带内；背景可过；
- 端点 clip 不超过 1–2 级。

---

## 5. Color Grading 怎么算

### 5.1 三段（Shadow / Mid / High）

- 目标：卡片的 `shadow.a/b`、`mid.a/b`、`high.a/b`；当前：批次同段。
- 增量：`Δab_s = ab_s_T - ab_s_0`，同理 mid/high。
- 解：`[Hue, Sat, Lum]`（每段）由 LR 的 `J_CG` 反解；`Lum` 只做小幅（不许当曝光）。
- `Blending/Balance`：由三段的**分界**（p25/p75）决定；不追“风格化”滑块。

### 5.2 HSL（肤色/环境）

- 肤色：`h_s`、`C_skin`、`L_skin`；用橙/黄两个 band；**只动肤色带**，不全局拉饱和。
- 环境：背景/服装的主色（卡片的 `bg`/`cloth` 的 h/C）；用对应 band；幅度小于肤色。
- **不和 Color Grading 抢**：Color Grading 管“阴影/高光的染色方向”，HSL 管“具体色相的局部修正”；同一色相只走一条。

### 5.3 肤色硬门

- 脸/皮肤：`C_rank` 落在卡片带内、`L_face` 在带内、`h_s` 不偏；背景过曝允许。
- 如果 Color Grading 让肤色偏色（`Δh > 5°` 或 `ΔC > 3`），回退到 HSL 或降权。

---

## 6. Engine 参数怎么算（只做介质）

- 介质 profile 给：卷/纸/颗粒/光晕/扫描/格式/化学。
- 卡片 delta 只落在：`print_exposure`、`wb`、`couplers.amount`、`film_chemistry.gamma`、`scanner.black/white`。
- 用 engine 探针（灰楔/色卡/平场/高光边/斜边）量 `J_engine`，解 `Δp = argmin ||W(f(base+J·Δp)-T)||²`。
- **不把 LR 的 placement 塞回 engine**；engine 保持 medium-only（`ff194eb`）。

---

## 7. 求解器：顺序、敏感度、迭代

```
1. 批次 baseline（RAW/中性）→ 量当前特征
2. WB / Exposure（Basic）
3. Contrast / Highlights / Shadows / Whites / Blacks（Basic，粗）
4. Tone Curve（形状）
5. Color Grading（三段）
6. HSL（肤色/环境）
7. 量残差；2–3 轮；每轮只动“残差最大且 owner 对应”的旋钮
8. 冻结；同批同参数；outliers 单独标
```

- 敏感度：`J_WB/J_Exp/J_Basic/J_Curve/J_CG/J_HSL` 来自 LR 滑杆探针（`cal_basic`/`cal_curve_jac`/`cal_grading`/`cal_hsl` 已有）；engine 的 `J_engine` 来自引擎探针。
- 损失：`Σ w_i ||f_i - T_i||² + λ||Δp||² + 约束`；**分位/关系/秩**加权，不用全图 CDF。
- 不过门就记 **debt**（哪一面、残差多少），不假装一样。

---

## 8. 之前踩过的坑（逐条对上）

| 坑 | 这条设计怎么避 |
|---|---|
| 全局中位数当 target | 用卡片分位 + 关系；批次同量 |
| 直方图/CDF 匹配 | 只用 5 个分位锚点 + 单调曲线；不套 CDF |
| 逐图 kNN / 逐图手调 | 批次级一个参数；outliers 只标不拉 |
| 脸框 p50 | 用脸/颊的 `L_face` + `C_rank`，不用脸框 |
| 背景高光硬压 | 硬门只脸/皮肤/主体；背景可过曝 |
| 用 amount 管 placement | amount 归 engine 介质；LR 用 Exposure/曲线 |
| 用 Clarity/Dehaze 补对比 | 归介质/曲线；LR 不补 |
| 用 Grain 补纹理 | 归 engine/介质 profile |
| 用 WB 改肤色 | 肤色走 HSL；WB 只对齐中性 |
| 用 Saturation 全局拉肤色 | HSL 橙/黄局部；环境分带 |
| 用 Color Grading 的 Lum 当曝光 | Lum 只做小幅染色；曝光归 Exposure |
| 用 per-channel 曲线调色 | RGB 一条；颜色归 CG/HSL |
| 大师 preset 套所有图 | 卡片 = target；介质 = base；参数解出来 |
| 以人为本 person PE 逐图 | 暂缓；LR Basic/Exposure 批次级落点 |
| 绝对 C 当“浓不浓” | 用 `ΔC/C_rank` + L；绝对 C 只参考 |
| 暖/冷标签当风格 target | 用 `shadow/high a/b` 关系；标签只做检索 |
| 只看中位/均值 | 看 p25/p75、p5/p95、关系、秩 |
| 一条曲线/一组参数走所有场景 | 匹配 → 卡片 → 参数；换批重解 |

---

## 9. 38/919 怎么落

- 匹配：38（104）top-1，用户确认。
- 目标：104 卡片的 `skin C/L/h`、`p25/p50/p75`、`shadow/high a/b`、`C_rank`。
- 批次：919 是 RAW camera WB + auto bright；当前 `C 8.5 / L 25.4`，暗部低；先按 **batch baseline** 归一到可测，再解。
- 先解 **WB/Exposure**（把 919 的脸 L 拉到 104 的 `L_face` 带），再 Basic 粗对比，再曲线，再 CG/HSL。
- 只在**探针/合成图**上验证；**不渲 919**，等设计定稿。

---

## 10. 下一步（不渲染）

1. 从 `directories.json`/`images.jsonl` 生成每卡 `target`（分位/关系/标签）。
2. 用现有 `cal_basic`/`cal_curve_jac`/`cal_grading`/`cal_hsl` 建 `J` 矩阵；缺的补探针。
3. 写求解器：WB→Exposure→Basic 粗→Curve→CG→HSL；2–3 轮；批次级。
4. 在探针/合成图上跑 38/104 的 target；只出参数和残差。
5. 不进 919 渲染。
