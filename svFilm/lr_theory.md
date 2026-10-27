# LR 理论基础（人像工具版）

> 原则：LR 是成熟工具，旋钮语义查文档，不靠“挪一下测一下”猜。
> 下面每条都对应我们求解器的实现约束。

---

## 1. Basic 面板：六个 Tone 旋钮不是独立的

来源：Adobe Lightroom Classic 帮助 · Basic/Histogram；Julieanne Kost 的 Basic 与 Tone Curve 教程。

- **Exposure**：整体曝光增益，主要动中间调，亮暗两端一起走；靠直方图和 clipping 警告判。
- **Contrast**：中间调对比，围绕一个 pivot 把亮部提亮、暗部压暗；同时影响高光/阴影。
- **Highlights**：高光区恢复/压缩，主要动亮部，但会带到中间调。
- **Shadows**：阴影区提亮/压暗，主要动暗部，也会带到中间调。
- **Whites**：白端（直方图右端）；**Blacks**：黑端（左端）。它们管端点，不是管整体。
- **Clipping**：直方图左右三角；Alt/Option 拖 Whites/Blacks 看逐通道剪切。
- **Auto Tone**：会同时改 Exposure/Contrast/Highlights/Shadows/Whites/Blacks/Saturation/Vibrance。

**对我们：**
- 六个旋钮必须**按区间分工**，不能都打同一个分位。
- Exposure 管脸落点；Whites/Blacks 管端点；Highlights/Shadows 管亮/暗区；Contrast 管中间调。
- 顺序：Exposure → Whites/Blacks → Highlights/Shadows → Contrast；每步量，别开环一遍。

---

## 2. Tone Curve：Parametric 与 Point，最多 16 点

来源：Julieanne Kost · The Tone Curve Panel in Lightroom Classic。

- **Parametric Curve**：Highlights / Lights / Darks / Shadows 四个区域滑杆；下面三角决定区域范围。
- **Point Curve**：**最多 16 个点**；可逐通道 R/G/B；Input/Output 数值框；可 Save。
- **Refine Saturation**：Point Curve 会同时改亮度和饱和度；这个滑杆控制饱和度改动量，0 = 只改亮度。
- **Targeted Adjustment**：在图上拖，直接改对应曲线点。
- 曲线在 Basic **之后**；Basic 已做的端点/中间调，曲线不应重复。

**对我们：**
- `run_v2` 的 21 点曲线**超了 16 点上限**，会被 LR 截断 → 改成 ≤16 点。
- 曲线只解**形状残差**（toe/shoulder/中间调），端点由 Whites/Blacks 锁死。
- 不做 per-channel 曲线 + 复合曲线叠加（会双重作用）；颜色交给 Color Grading/HSL。
- `Refine Saturation` 默认 100；如果只要亮度形状，设 0。

---

## 3. Color Grading：三段 + blending/balance

来源：Adobe Lightroom Classic 帮助（Color Grading / Color Mixer）。

- 三段：**Shadows / Midtones / Highlights**；每段 **Hue / Saturation / Luminance**。
- **Blending / Balance**：控制三段的分界与混合。
- 它是**染色**，不是曝光；Luminance 只做小幅，不能当曝光用。

**对我们：**
- 三段目标来自卡的 `shadow/mid/high a/b`（新 `card_target_v2` 已有）。
- 只用三段染色；不要再用 Split Toning 另开一套。
- 键名先用 `KEYS`/`GETVAL` 读回验证（`ColorGradeShadowHue`/`SplitToningShadowHue` 不要猜）。

---

## 4. HSL / Color Mixer：8 个色相带

来源：Adobe Lightroom Classic 帮助 · Color Mixer；Basic 教程。

- 8 个带：Red / Orange / Yellow / Green / Aqua / Blue / Purple / Magenta。
- 每个带 **Hue / Saturation / Luminance**。
- 肤色主要在 **Orange/Yellow**；环境在 Green/Blue；不要全局 Saturation 一刀切。

**对我们：**
- 肤色只走 Orange/Yellow；环境走 Green/Blue；幅度小于肤色。
- HSL 和 Color Grading 不抢同一色相：CG 管三段染色，HSL 管具体色相。
- 目标：卡 `skin h/C`、`shadow/high a/b`。

---

## 5. 人像的特殊性（我们工具的目的）

- **脸是硬门**：脸/皮肤不过曝、不偏色；背景过曝允许。
- **落点看脸，不看全图**：Exposure 对 `L_skin`，不是 p50。
- **白裙/天空不参与端点靶**：Whites/Blacks/Highlights 只对**前景**（人物）分布；背景单独存。
- **肤色是局部**：HSL Orange/Yellow；不做全局 Saturation。
- **批次级**：同一批同一组参数；outliers 只标不拉。

---

## 6. 求解器实现约束（从理论直接来）

1. **一个特征一个 owner**：
   - 脸 L → Exposure
   - 前景 p99 → Whites
   - 前景 p95 → Highlights
   - 前景 p5 → Shadows
   - 前景 p1 → Blacks
   - 前景 p25–p75 → Contrast
   - 形状/toe/shoulder → Point Curve（≤16 点）
   - 三段染色 → Color Grading
   - 肤色 h/C → HSL Orange/Yellow
2. **顺序**：Exposure → Whites/Blacks → Highlights/Shadows → Contrast → Curve → CG → HSL。
3. **迭代**：每轮只动残差最大的一个 owner；2–3 轮；其余锁死。
4. **曲线 ≤16 点**；端点锁；单调。
5. **键名验证**：写前 `KEYS`/`GETVAL` 读回，不猜。
6. **测量域**：卡和渲染都在 1080 sRGB JPEG 上量。
7. **前景/背景**：人物 mask 分离；背景不进端点靶。

---

## 7. 参考

- Adobe Lightroom Classic Help · How to adjust image tone and color：  
  https://helpx.adobe.com/lightroom-classic/desktop/process-and-develop-photos/image-tone-color.html
- Adobe Lightroom Classic Help · Color Mixer (HSL)：  
  https://helpx.adobe.com/lightroom-classic/desktop/process-and-develop-photos/color-mixer.html
- Julieanne Kost · The Tone Curve Panel in Lightroom Classic（Parametric/Point，Point 最多 16 点，Refine Saturation）：  
  https://jkost.com/blog/2024/08/the-tone-curve-panel-in-lightroom-classic-2.html
- Julieanne Kost · Using the Basic Panel and Histogram（Tone 滑杆、clipping、Auto Tone）：  
  https://jkost.com/blog/2024/08/using-the-basic-panel-and-histogram-to-exposure-and-contrast-in-lightroom-classic.html
- Adobe Learn · Make basic edits to adjust lighting and color：  
  https://www.adobe.com/cn/learn/lightroom-cc/web/adjust-image-lighting-color-lightroom-cc
