# design/ · 怎么用

> **两份真源：`纪律.md`（先读）+ `设计真源.md`**（历史版本在 git）。
> 本目录只放这六份：

| 文档 | 管什么 |
|---|---|
| `纪律.md` | 铁律 · 归因 · 每轮动作（每次动手前必读） |
| `设计真源.md` | 设计 + 分工 + 判据（唯一） |
| `LR设计.md` | LR A/B/C 怎么做 |
| `LR数值来源.md` | 每个数从哪来、哪些是分析、哪些是生产 |
| `引擎设计.md` / `测量设计.md` | 引擎与测量口径 |

一句话：**引擎交一张不偏色的底片；LR 决定它最终多亮、什么颜色；人拍板。**

---

## 两条硬边界

```
① 引擎不管脸、不管落点 —— 曝光是 LR 的事。
② 算值不依赖 LR —— 公式要能查、能离线复算；只有出 ③ 成片才驱动 LR。
```

---

## 跑

```powershell
$py = "C:\Users\psw99\.workbuddy\binaries\python\envs\spektrafilm\Scripts\python.exe"
cd E:\svStudio\svFilm

# 测试（纯函数，不渲不碰 LR）
& $py -X utf8 design\tests\test_design.py

# 看卷/卡
& $py -X utf8 -m design.cli stocks
& $py -X utf8 -m design.cli cards

# 第一段：整批渲基线 → 匹配 → 出选卡页（**不驱动 LR**）
& $py -X utf8 -m design.cli run --raf "D:\照片库\<批>" --out <输出目录>

# 人拍板：打开 <输出目录>\match\匹配页.html，点某张样片 = 选卡 + 选 hero（设计 §3.4）
# 第二段：按这张卡整批渲 ① → LR 出 ③ → 量 → 验收 → 目检
& $py -X utf8 -m design.cli run --raf "D:\照片库\<批>" --out <输出目录> --card <卡名> --hero <样片> --export

# 只重出选卡页（不重渲；只花测量钱，约 1~2 秒/张）
& $py -X utf8 -m design.cli page --out <输出目录>
```

---

## 生产链（只有这五步）

```
① 引擎出片
A 批级：batch_grade → 全批中位 → 一条曲线 + 颜色方向
B 图级：ev_for_l（曝光）· ev_for_face（局部）· 白端/饱和/冷暖 = 方向 + 一档
C：XMP → ③ 成片 → 量 → 验 → 人看
```

**没有公开传递函数的旋钮**（Highlights/Shadows/Whites/Blacks/Contrast、局部饱和、
局部 Shadows、冷暖）：**方向 + 一档（10 UI）+ ③ 验**，不建模、不外推。
分析模型（k(L)/borrowed）只在 `analysis/corpus/kurai/_lr_models/`，**生产不 import**。
