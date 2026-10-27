# 卷 / 纸怎么选，旧数据怎么用，卷子怎么匹配

> 先纠正一个层级：**卷/纸不是每张卡选的，是介质层选的。**  
> 64 张目录卡是“风格/情境”；3 个介质 profile 才是“卷 + 纸 + 颗粒 + 光晕 + 格式”。  
> 匹配选卡；卡属于哪个语料，就用哪个介质 profile；卷/纸是**基础**，不是滑块。

---

## 1. 现在的 3 个介质 profile

| profile | 卷 | 纸 | 格式 | 用途 |
|---|---|---|---|---|
| `kurai_v1`（鹿井） | `kodak_portra_400` | `kodak_portra_endura` | 35mm | 鹿井 62 个目录 |
| `hamada_v1`（滨田） | `kodak_portra_400` | `kodak_portra_endura` | 6×7 / 70mm | 滨田 22 张 |
| `xhs_v1`（xhs） | `kodak_gold_200` | `kodak_portra_endura` | 35mm | xhs 90 张 |

- 这是 10-17/10-18 拍板的结论；**20>17 的替代方案目检不采**。
- 卷/纸是 **params 构建时传入**的，overrides 改不了；换卷/纸 = 换介质 profile，**不是**拉一个滑杆。
- 卷/纸不参与每张卡的风格求解；它只提供 base（卷表/纸表/颗粒/光晕/化学/扫描形状）。

---

## 2. 旧数据能不能直接用

### 能直接用（而且应该用）

| 旧数据 | 怎么用 |
|---|---|
| 卷/纸结论（portra_400 + endura；xhs gold_200 + endura） | 就是 3 个介质 profile 的 base |
| 3 个 `preset.json` 的卷/纸/颗粒/光晕/化学/格式/WB/PE/amount | 介质 profile 的初值；不重新发明 |
| 10-17/10-18 的卷纸/纸 A/颗粒/光晕测试 | 作为 base 的证据；换卷/纸时的对照 |
| `cal_basic` / `cal_curve_jac` / `cal_grading` / `cal_hsl` | **LR 求解器的敏感度矩阵**，直接复用 |
| 旧渲染样张 / `_snap` | 介质 base 的目检参照；不是 target |
| 旧 `鹿井候选*` 预设 | **实验**，不是生产；可当“换卷/纸/参数”的对照 |

### 不能直接用

| 旧数据 | 为什么 |
|---|---|
| 旧的 master targets / `targets.json` / 每条预设的靶带 | 已被 `ff194eb` 删除；新架构的 target 是**卡片分布**，不是大师全局带 |
| “大师预设”当一套 look 套所有图 | 现在卡是单位；大师只是语料/介质来源 |
| 旧 52 个鹿井目录 / 旧 46 门 | 新语料是 62 目录 / 64 单元；旧子集不能当全量 |
| 旧 face/cheek 测量（脸框、C≤22 截断） | 新 skin v2 + 相对/秩；旧值不可比 |
| 旧 person PE / 以人为本 | 暂缓；不回到 engine |
| 旧 per-image 调参 / 逐图 kNN | 现在是批次级；卡片 target |
| 旧 `_smoke_919` / 私人测试产物 | 已清；隐私 |

**一句话**：旧数据里，**“介质”和“测量/敏感度”可以直接用；“大师靶带/大师预设/旧子集”不能当新 target。**

---

## 3. 卷子怎么匹配

卷/纸是**离散选择**，不是连续求解。顺序：

1. **用户 override**：用户明确说“我拍的是 Portra 400 / 我要 Gold 200 的 look” → 直接用对应 profile。
2. **匹配卡的语料**：卡属于鹿井→`kurai_v1`；滨田→`hamada_v1`；xhs→`xhs_v1`。  
   这是默认路径；64 卡里绝大多数是鹿井，所以默认 `kurai_v1`。
3. **卡的颜色/颗粒签名**（只在语料混合/未知时用）：
   - `shadow/high a/b`、`warm_cool`、`C_rank`、颗粒的粗细；
   - 在 3 个 profile 的**base 签名**里选最近的一个；
   - 只做**粗选**，不逐卡换卷。
4. **输出意图**：用户要“更像 xhs 的 Gold 暖黄”还是“鹿井的 Portra 肤色” → 换 profile；卡仍是风格 target。

**不做什么：**

- 不按每张卡选卷；64 卡不是 64 种卷。
- 不把卷/纸当 LR 滑杆；它是介质。
- 不在一次批次里混卷（同批同 profile）。
- 换卷/纸后必须**重跑 base**（卷表/纸表/颗粒/光晕/化学/扫描形状都变），LR 的 `J` 也要复核；不是改一个值。

---

## 4. 卷/纸和 engine/LR 的分工

```
卷/纸 = 介质 profile（base）
  → engine：卷表/纸表/颗粒/光晕/化学/扫描/格式
  → LR：在 base 上做 placement + look（Basic/Curve/CG/HSL）

卡 = 风格 target（分布/关系/情境）
  → 决定 engine 的 print_exposure / wb / amount / gamma / black/white
  → 决定 LR 的 Basic/Curve/CG/HSL
```

- 卷/纸不进入 LR 的损失函数当变量；它是**固定 base**。
- 卷/纸变了，target 不变，但 base 变；engine/LR 的参数要重解。
- 卷/纸的选择**先于**参数求解；不是求解器的一部分。

---

## 5. 旧数据 → 新流程的映射

| 新流程 | 旧数据 |
|---|---|
| 介质 profile | 3 个 `preset.json` 的卷/纸/颗粒/光晕/化学/格式/WB/PE/amount |
| engine 探针/敏感度 | 旧卷纸/纸 A/颗粒/光晕测试 + `_snap` |
| LR 敏感度 | `cal_basic`/`cal_curve_jac`/`cal_grading`/`cal_hsl` |
| 卡片 target | 新的 859 测量 / 64 卡（不是旧 master targets） |
| 匹配 | 新的 ResNet50 嵌入 + 保留标签（38 已验证） |
| 卡风格 | 新卡分布；不是“大师预设” |

---

## 6. 防坑

- ❌ 把卷/纸当“风格滑块”逐图调。
- ❌ 用旧 master targets 当新 target。
- ❌ 用旧 52 子集/旧脸框/旧 person PE。
- ❌ 换卷/纸不重跑 base、不复核 `J`。
- ❌ 同批混卷。
- ❌ 拿“暖/冷”标签选卷；卷是介质，不是色温标签。
- ❌ 把 `鹿井候选*` 当生产；它们只是实验。
- ❌ 卷/纸进 LR 损失当连续变量；它是离散 base。

---

## 7. 下一步

1. 把 3 个介质 profile 固化成 `medium_profiles.json`（卷/纸/颗粒/光晕/化学/格式/扫描）。
2. 用旧卷纸/纸 A/颗粒测试补 `base signature`（供“混合语料”粗选）。
3. 写 `stock_match` 决策树（override → 语料 → 签名 → 默认）。
4. 换卷/纸的 **重跑 base + 复核 J** 流程写成脚本契约。
5. 不渲染；先在 38/104 上做“同卡、换 profile”的 base 对照。
