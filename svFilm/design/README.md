> 🔒 **没有我的授权，不准去动。**
>
# design/ · 怎么用

> **两份真源：`纪律.md`（先读）+ `设计真源.md`。**
> 历史版本在 `_archive_20261101/`。

| 文档 | 管什么 |
|---|---|
| `纪律.md` | 最高规则 · 归因 · 每轮动作（动手前必读） |
| `设计真源.md` | 目的 / 架构 / 流程 / 卡 / 判据 |
| `LR设计.md` | 目录级 WB + 图级顺序怎么做 |
| `引擎设计.md` / `测量设计.md` | 引擎与测量口径 |

一句话：**选卡 → 引擎交一张不偏色的底片 ① → LR 对齐卡 → 人拍板。**

---

## 两条硬边界

```
① 引擎不管脸、不管落点 —— 曝光/明暗是 LR 的事。
② 算值不依赖 LR —— 公式可查、可离线复算；只有出 ③ 才驱动 LR。
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

# 第一段：读机内 JPG 做内容匹配 → 出选卡页（不渲 ①、不驱动 LR）
& $py -X utf8 -m design.cli run --raf "D:\照片库\<批>" --out <输出目录>

# 人拍板：在 <输出目录>\match\内容匹配.html 选卡；可指定 hero
# 第二段：按卡渲 ① → LR 出 ③ → 量 → 验收 → 目检
& $py -X utf8 -m design.cli run --raf "D:\照片库\<批>" --out <输出目录> --card <卡名> --hero <样片> --export

# 只重出选卡页（不重渲）
& $py -X utf8 -m design.cli page --out <输出目录>
```

---

## 生产链

```
① 引擎：按卡出介质底片（只渲一次；没有 ①′）；PE 按内容目录标
A 批级：只报护栏（曲线 IQR/反折/预算），不下发公共曲线
B 图级：WB（目录级，整批已定）→ Basic 明暗（曲线补）→ 肤色（蒙版里的 Point Color；不做 HSL）→ 一个局部蒙版
C：出 ③ + 目检页 → 人目检（唯一验收；机器不判过/不过）
```

**没有公开传递函数的旋钮**：可以用，但剂量由卡和目检决定，不写死“一档 10 UI”当目标。
分析模型只放 `analysis/`，生产不 import。
