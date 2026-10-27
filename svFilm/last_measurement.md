# 最后一次测量：介质、风格、LR 动态参数

> 目标：一次测全，存下来，后面不再重复读图。  
> 不再“先预设再补”，而是：**匹配目录 → 成片完整测量 → 反解引擎/LR 参数**。

---

## 1. 怎么理解“胶片风格”

胶片风格不是一张图的颜色，是四层叠出来的：

```
风格 = 介质（卷/纸/化学/颗粒/光晕/扫描/画幅）
     + 曝光/白平衡（placement）
     + 情境（光位/场景/服装/人物）
     + 卡的分布（皮肤/影调/分级/关系/秩）
```

- **介质**：决定“像不像胶片、哪卷哪纸”的物理底。
- **曝光/WB**：决定落点；同一卷不同曝光，看起来是两套。
- **情境**：光是硬/柔、顺/逆、室内/室外；这部分**不能靠调色复制**，只能靠匹配选对卡。
- **卡的分布**：同一卷、同一光，不同目录仍有风格差（001 街拍 / 013 暖咖啡馆 / 021 居家）；这是 target。

**所以：匹配负责“情境”；测量负责“介质 + 分布”；engine/LR 负责“把介质和分布渲出来”。**  
**没有任何预设**；3 个 profile 只是介质候选锚点。

---

## 2. 已测的（审计）

| 已有 | 在哪 | 覆盖 |
|---|---|---|
| 皮肤 v2：C/Cfull/L/h、ΔC、C_rank | `images.jsonl.skin_v2` / `directories.json.skin` | 64 卡 / 859 图，脸颊、宽门、相对/秩 |
| 影调/分级：p50/contrast/black5/white95/neutral/c50/c90/shadow_a/shadow_b/high_a… | `images.jsonl.style` / `directories.json.style` | 64 卡 |
| 皮肤相对量 `skin_rel` | `directories.json` | 64 卡 |
| 匹配特征：C/L/h、p50、contrast、bg、cloth、light_ratio、warm_cool | `match_features.json` | batch_A 10 张 |
| 嵌入、卡片 sheet、标签、validation、retrieval | `full/` | 64 卡 / 859 图 |
| 3 个介质 profile | `medium_profiles.json` | 3 个语料（**预设值，不是每卡测量**） |
| LR Basic 增益 | `calibration/basic_gain.json` | 一张图的 steps（**只有 Basic，缺 Curve/CG/HSL**） |

**结论：皮肤/影调/分级的“风格分布”有了；介质签名和 LR 动态参数基本没有。**

---

## 3. 还缺什么（这次要补）

### 3.1 每卡的介质签名（最大缺口）

| 维度 | 要量 | 现状 |
|---|---|---|
| 颗粒 | 高频噪声 rms、blur（染料云）、dye µm、逐通道 | ❌ 只有 3 个 profile 的预设值 |
| 光晕 | 高光边 red glow 强度/半径、diffusion | ❌ 预设值 |
| 纸/卷基色 | shadow/mid/high a/b、neutral、皮肤 h/C、白点、cross-talk | ⚠️ 只有风格分位，没有“介质响应” |
| 化学 | gamma、红黄配比、couplers 彩度分配、通道 cross-talk | ❌ 预设值 |
| 曝光/印相 | L 分布、脸 L、PE 落点 | ⚠️ 风格有 L，但没反解 PE |
| WB | 同图中性 a/b、皮肤 h | ⚠️ 部分 |
| 黑白端 | black5/white95、clip、端点形状 | ⚠️ 风格有，没作为介质约束 |
| 扫描 | MTF/锐化半径/柔度、scanner USM | ❌ 预设值 |
| 画幅 | 长边、DoF、颗粒尺寸 | ❌ 预设值 |
| 曲线 | p5/p25/p50/p75/p95、toe/shoulder | ⚠️ 有分位，没形状 |

### 3.2 每卡的引擎参数（反解结果）

- `card_engine_<id>.json`：卷/纸标签 + 所有数值（PE/WB/amount/gamma/grain/halation/scan/black/white/format）。
- 现在没有；`medium_profiles` 是 3 个语料级预设，不是每卡。

### 3.3 每卡的 LR 目标与参数

- `card_lr_<id>.json`：Basic/Curve/CG/HSL 的 target 与解出的滑杆值。
- 现在只有 `basic_gain.json`（Basic 的 steps），**缺 Curve/CG/HSL 的敏感度**。

### 3.4 局部 / 情境 / 质量

- 局部：脸/眼/高光/局部对比；现在只有颊和整体。
- 情境：光位、场景、服装、face%/env%/light_ratio；现在有部分（tags/match），没进卡 target。
- 质量：锐度/曝光/脸质量/outliers；现在只有 face_small/suspect。

---

## 4. 介质怎么测

### 4.1 颗粒

- 在**平坦区**（天空/皮肤/中性墙）做高通，避开细节/边缘/文字。
- 量：`rms`（噪声幅度）、`autocorr` 半高宽（颗粒尺寸）、`blur`（染料云）、逐通道。
- 排除 JPEG/降噪；记录出图长边；同卡多张取中位。

### 4.2 光晕

- 找**高光边**（白裙/窗/灯），量红通道在边外的衰减：`强度/半径`。
- 与镜头 diffusion 分开；只取“高光溢出”那一段。

### 4.3 纸/卷基色

- 同图中性（低 C 像素）的 a/b；皮肤的 h/C；shadow/mid/high 的 a/b。
- 量**相对关系**：皮肤-中性、阴影-高光、白点-黑点；不做绝对色温。

### 4.4 化学

- gamma：红/黄/蓝的响应比；couplers：C 随密度的分配；cross-talk：通道串扰。
- 用引擎探针（色卡/灰楔）对照；不要从名字推。

### 4.5 曝光/WB/黑白端

- L 分布、脸 L、PE 落点；同图中性；black5/white95/clip。
- 这些与风格分位共用一次测量。

### 4.6 扫描/画幅

- MTF（斜边）、锐化半径、柔度；画幅长边；颗粒尺寸随画幅缩放。

### 4.7 曲线

- p5/p25/p50/p75/p95 + toe/shoulder；不是 CDF。

---

## 5. LR 动态参数怎么补

- **目标**：每卡的 Basic/Curve/CG/HSL 目标 = 同一张卡的分布（不需要另测风格）。
- **敏感度**：`cal_basic`（已有 `basic_gain`）+ 缺的 `cal_curve_jac` / `cal_grading` / `cal_hsl`。
  - 曲线：滑杆 ±Δ → 分位变化。
  - CG：三段 hue/sat/lum ±Δ → shadow/mid/high a/b 变化。
  - HSL：橙/黄/绿/蓝 ±Δ → 皮肤/背景 h/C 变化。
- **解**：同一目标，约束最小二乘；每卡一组 LR 参数；不预设。
- **验收**：特征残差 + 批次一致；不看“滑杆像不像”。

---

## 6. “最后一次测量”清单

一次跑完，存 `card_medium_<id>.json` / `card_engine_<id>.json` / `card_lr_<id>.json` / `card_target_<id>.json`：

- [ ] 皮肤 v2 + 风格分位（已有，复用）
- [ ] 颗粒 rms/blur/dye/逐通道
- [ ] 光晕 强度/半径
- [ ] 纸/卷 基色 + 皮肤 h/C + 中性 + cross-talk
- [ ] 化学 gamma/红黄/couplers
- [ ] 曝光/PE/脸 L
- [ ] WB/中性/皮肤 h
- [ ] black5/white95/clip
- [ ] 扫描 MTF/USM/柔度
- [ ] 画幅/DoF/颗粒尺寸
- [ ] 曲线 p5–p95/toe/shoulder
- [ ] 局部 脸/眼/高光
- [ ] 情境 光位/场景/服装/face%/env%
- [ ] 质量 锐度/曝光/outliers
- [ ] LR 敏感度 `cal_curve_jac` / `cal_grading` / `cal_hsl`
- [ ] 反解 engine / LR 参数

**每卡 16 张取分布；outliers 只标不拉；同批同参数。**

---

## 7. 下一步

1. 先对 38/104 的 16 张跑完整清单（介质 + 局部 + 情境 + 质量）。
2. 补 LR 的 `cal_curve_jac` / `cal_grading` / `cal_hsl`（滑杆探针）。
3. 反解 `card_engine_38` / `card_lr_38`。
4. 用这组参数渲 3 张 batch_A，对 104；不整批。
5. 这一轮之后，测量只增量，不重测。
