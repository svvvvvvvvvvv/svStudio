# design/ · 怎么用

> **唯一真源 = `设计真源.md`**（纪律已并入第一部分；历史版本在 git）。
> 本目录只放这五份：

| 文档 | 管什么 |
|---|---|
| `设计真源.md` | 纪律 + 分工 + 判据（唯一） |
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

# 全链（引擎 → LR → 成片 → 验收）
& $py -X utf8 -m design.cli run --raf "D:\照片库\<批>" --out <输出目录> --yes --export
```

---

## 生产链（只有这五步）

```
① 引擎出片
A 批级：batch_grade → 全批中位 → 一条曲线 + 一套颜色
B 图级：ev_for_l（曝光）· ev_for_face（局部）· 白端/饱和/冷暖 = 方向 + 一档
C：XMP → ③ 成片 → 量 → 验 → 人看
```

**没有公开传递函数的旋钮**（Highlights/Shadows/Whites/Blacks/Contrast、局部饱和、
局部 Shadows、冷暖）：**方向 + 一档（10 UI）+ ③ 验**，不建模、不外推。
分析模型（k(L)/borrowed）只在 `analysis/corpus/kurai/_lr_models/`，**生产不 import**。
