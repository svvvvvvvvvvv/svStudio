# LR 数值来源

> 生产只允许两类数：**单位换算** + **标准定义式**。
> 没有公开传递函数的旋钮：**方向 + 一档 10 UI + ③ 验**，不建模、不外推。
> 分析模型只在 `analysis/corpus/kurai/_lr_models/`，生产不 import。

## 1 单位换算

| 换算 | 值 | 依据 |
|---|---|---|
| `LocalExposure2012` 文件值 | EV/4 | autoshade 对真实 XMP 实测 |
| 局部其余滑杆文件值 | UI/100 | 同上 |
| 全局 Exposure | +1 = 线性光 ×2 | Adobe f-stop |
| 曝光 → L\* | `L' = 2^(EV/3)(L+16) − 16` | CIELAB 定义 |
| L\* → 8-bit 码 | sRGB 传输函数 | IEC 61966-2-1 |
| 提亮后的分位 | `Q_p(g(X)) = g(Q_p(X))` | 单调映射 |

L\*=50 的 sRGB 码是 **119**，不是 127.5；1 EV @ L50 精确 +17.2。

## 2 一档政策

| 旋钮 | 触发 | 剂量 |
|---|---|---|
| 全局 Whites | p95 超卡肩 | −10 UI |
| 局部 Saturation | 脸 C 出带 | ±10 UI |
| 冷暖 | `warm_cool_b` 出带 | 两轮各 10 点 |
| 局部 Shadows | — | 只记账；要动 10 UI |
| 肤色色相 | 出带 | 只记方向 |

一档 = 方向 + 可验收的最小步，不是“正确值”。

## 3 已删（别再回来）

```
0.221 C*/单位 · 18.7 L*/档 · 11.2 p99/档 · 0.147/7.0/14.7 L*/点
0.15 Blacks · 0.30 曲线暗部 · 0.18 b*/点 · 0.84 · −30
收白 ×4 / 2..30 · 冷暖 带宽×2.5/×1.5 · mid_b/ratio · PHYS_P95=96
```

## 4 分析模型

只放 `analysis/corpus/kurai/_lr_models/`，**不进生产**；不在这里展开。

## 5 已有实现（先看）

- [rawmakase](https://github.com/pch/rawmakase/blob/main/docs/tone-controls.md)（MIT）：Camera Raw Basic 面板系统实测；Highlights/Shadows 是局部算子，滑杆组合不能简单相加。
- [autoshade](https://github.com/skymanbp/autoshade)（MIT）：面向 LR 的 tone model；是拟合，不是 Adobe。
- [Adobe tone controls](https://helpx.adobe.com/lightroom-classic/desktop/help/tone-control-adjustment.html)：只有定性，无传递函数。

## 6 代码索引

| 文件 | 管什么 |
|---|---|
| `photometry.py` | 亮度/码/曝光/白端 |
| `batch_grade.py` | A 段：一条曲线 + 一套颜色 |
| `curve_design.py` | 批级曲线（S3 预算、端点、p95 cap） |
| `lr_solve.py` | B 段：曝光/局部/白端 |
| `lr_exec.py` / `lrsink.py` | plan → LRPlan → XMP/桥 |
| `review.py` | ③ 验收 |
