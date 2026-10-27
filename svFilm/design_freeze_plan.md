# 设计冻结计划：先看最近怎么横跳，再决定这次怎么一次定下来

> 依据：`svFilm/迭代记录.md`、`git log`（到 `f583dff`）、`引擎手册.md`、`AGENTS.md`、以及 10-20 之后的新设计文档。  
> 目的：把“机制/设计”一次冻结，避免再出现“加 → 撤 → 再议 → 暂停”的循环。

---

## 1. 最近的横跳（有据可查）

| # | 横跳 | 走向 | 代价/教训 |
|---|---|---|---|
| 1 | **影调/颜色/肤色/认人认脸层** | 建层 → `drop-tone-and-skin`（09-29）→ `drop-grade`（10-10）→ `ff194eb` 只留介质（10-21） | 每层都曾“看起来必要”；最后全删；owner 没定就先建 |
| 2 | **master targets / 场景靶 / stock 靶** | `targets.json` + `_scene_engine` + `_stock_engine` → 预缩减到一条（10-15）→ `ff194eb` 全删（10-21） | 靶带是“大师风格”的替身；新卡出来后整层作废 |
| 3 | **master 家族** | `family_v1/v2`、`v15`、`vibrance-30`、`hist5/hist6`、每图 shadow/blacks → 全删 | 每图/每 histogram 的 master 都是“追一张图”；没有单位 |
| 4 | **person PE / 以人为本** | 10-16/10-19 落地 → 10-21 `ff194eb` 撤 → 10-27 再议 → 10-27 暂停 | ~840 行删了又议；engine vs LR 的 placement owner 没定 |
| 5 | **v8 主链** | `layer1`/`layer2`/`layer5`/`basic_qp`/`qp`/`apply_layer2` → v9 `basic`/`layer1_v9` → 10-27 清理 | 两套链并行很久；旧链删掉前一直占着维护面 |
| 6 | **测量域/肤色** | 900/1200 → 1080；脸框 p50/C≤22 → 颊/宽门/相对/秩；C 42.7 → 27.6 | 同一张脸换域差 8 L*；绝对 C 当“浓不浓”会错 |
| 7 | **标签粒度** | v0.1 粗 → v0.2 细 → 64 卡 → 双标 κ 0.54 → 删 subtype/composition/color_env | 细标签好看但不可靠；最后靠嵌入 |
| 8 | **匹配器** | 12 个低层特征 top-3 40.5% → ResNet50 top-3 78.7% | 先做“可解释特征”，再发现嵌入才是主力 |
| 9 | **卷/纸** | 10-17 portra_400+endura → 10-18 xhs gold_200 → 20>17 替代不采 → 候选 9 个 | 卷/纸是介质，不是每卡风格；候选只是实验 |
| 10 | **分支** | `feat/lrfinish` → `feat/person-pe` → `chore/cleanup-lrfinish` | 分支名跟着“想法”走，不跟“设计”走 |

**共同点：**

- 都是“**先实现，再定 owner**”：person PE、targets、master 家族、标签、卷/纸。
- 都是“**单位漂移**”：大师 vs 卡片；52 vs 62；900 vs 1080；engine vs LR。
- 都是“**追一张图/一个眼**”：C 42.7、warm、某个 master、某个 histogram。
- 都是“**没有决策日志/冻结门**”：文档散、`迭代记录` 停在 10-20、新决策在聊天里。
- 都是“**一次改太多**”：`ff194eb` 一次删 840 行 + 整层；新设计又从零起。

---

## 2. 这次一次定下来的方法：先立宪，再冻结

### 2.1 十条不变量（先拍死，不再议）

1. **卡是风格单位**；介质 profile 是渲染底；**大师不是预设**。
2. **engine 管介质，LR 管 placement + look**（`ff194eb` 的分工）。
3. **一个特征一个 owner**；两个旋钮都能动它时，只留代价小的那个。
4. **card target = 分布 + 关系 + 秩**；不用全局中位数、不用 CDF/直方图、不用绝对 C 当“浓不浓”。
5. **批次级**；不逐图 kNN、不逐图手调；outliers 只标不拉。
6. **硬门只脸/皮肤/主体**；背景过曝允许。
7. **标签只做检索/解释**；不是风格 target；场景子类是检索层，不是渲染层。
8. **卷/纸 = 介质**；3 个 profile；不按每张卡选卷；换卷 = 换 base + 重跑 `J`。
9. **person PE 不加**；脸/服装落点在 LR placement，不在 engine 第二遍印相。
10. **不渲染直到设计冻结**；冻结前只做探针/合成图；隐私/私人批次不进共享产物。

> 这 10 条是“宪法”。任何一条要改，必须走 §2.4 的变更控制，不许在聊天里顺手改。

### 2.2 决策登记（ADR）

`decisions.md` / `decisions.json`，每条：

```
id, title, status(open|closed|superseded), owner, date,
context, decision, evidence, consequences,
reopen_conditions, supersedes/superseded_by
```

- **closed 的条目**：没有新证据不许重开；新证据要写“这次基础哪里变了”。
- **open 的条目**：进 P0/P1，有 owner、截止、验收标准。
- 所有设计文档引用 ADR id；不再“文档里一句话、聊天里一个决定”。

### 2.3 接口/契约（先定形状，再填内容）

| 契约 | 内容 |
|---|---|
| `card_target.json` | 每卡的 target：skin/tone/grade/cloth/bg/comp + tags + 分位/关系/秩 |
| `medium_profiles.json` | 3 个 profile：卷/纸/颗粒/光晕/化学/格式/扫描/WB/PE/amount |
| `solver` | 输入 target + batch baseline + medium；输出 engine/LR 参数；带 residual/debt |
| `match` | 输入 batch；输出 top-3 卡/子卡 + 证据；不编辑 |
| `tags` | 保留/降级清单；检索用；不参与渲染 |
| `data` | `images.jsonl`/`directories.json`/`cards/`/`validation`/`match`；只读 |
| `privacy` | 私人批次用 `batch_A`/`IMG_*`；不进仓库/共享报告 |

### 2.4 冻结门（P0 全绿才 freeze）

- [ ] 10 条不变量写进 ADR，用户拍板。
- [ ] `card_target` schema + 64 卡 target 生成。
- [ ] 3 个 medium profile 固化；卷/纸/颗粒/光晕/化学/格式/扫描齐。
- [ ] engine/LR 分工写死；person PE 关闭。
- [ ] LR Basic/Curve/CG/HSL 的 owner + 求解顺序 + `J` 矩阵。
- [ ] 卷/纸匹配决策树（override→语料→签名→默认）。
- [ ] 匹配：嵌入 top-3 + 标签证据；38 已确认。
- [ ] 标签保留/降级清单；embedding 为主、标签为辅。
- [ ] 探针/合成图的 A/B 判据；不渲批次。
- [ ] 隐私/私人批次规则。
- [ ] `design_v1.0.md` + `decisions.md` + 测试（`test_v9` 全绿）。

### 2.5 变更控制（freeze 之后）

- 只接受 **bug/事故**；新功能/新决策走 **ADR + 版本号 + 重跑契约测试**。
- 不许“顺手改一个值”；不许“聊天里拍板”；不许“分支名跟想法走”。
- 任何 re-open 必须写：**这次基础哪里变了**（新数据/新证据/用户改目标）。
- 每次 freeze 打 tag：`design-v1.0`；后续 `v1.1` 只含 ADR 列表。

### 2.6 A/B / spike 规则

- **先写判据再跑**：指标、样本、阈值、停止条件；没有判据的 A/B 不算数。
- **spike 有时限**：到点就停；产物进 `spike/`，不进主链。
- **不拿“看着像”当验收**：看 residual/批次方差/硬门；目检只做最后确认。
- **一次只改一个 owner**：多旋钮同时动 = 无法归因。

---

## 3. 现在哪些是 closed，哪些还必须开

### closed（不再议）

- 3 个介质 profile：鹿井/滨田 portra_400+endura；xhs gold_200+endura。
- engine 管介质、LR 管 placement+look。
- person PE 不加。
- 64 卡是风格单位；大师不是预设。
- 测量域 1080；skin v2 + 相对/秩。
- 匹配用嵌入为主；标签只做检索/解释。
- 标签保留：光/服装/地点/背景类型；删：scene.subtype/composition/color_env/activity/position。
- 隐私：私人批次不进仓库/共享报告。

### open（P0，freeze 前必须关）

1. **card_target 的精确特征**：哪些分位/关系/秩入 target；situation 怎么归一。
2. **LR Basic/Curve/CG/HSL 的求解顺序与 `J`**：谁先谁后；残差怎么分给谁。
3. **engine 的 `PE/WB/amount/gamma/black-white` 与 LR 的边界**：哪些只在 engine，哪些只在 LR。
4. **卷/纸匹配**：语料/override/签名；混合语料怎么办。
5. **匹配的 batch profile**：多图怎么聚合；top-3 怎么报；sub-card 什么时候进。
6. **xhs 的 medium**：gold_200 是否最终；候选 9 个怎么处理。
7. **隐私/共享**：哪些报告可外发；`batch_A` 规则。

### P1（freeze 后再做）

- 场景子卡；64 卡子卡聚类。
- L3 嵌入扩到 batch/子卡；top-3 80% 目标。
- 工具壳（卡浏览器/标签/匹配报告）。
- 渲染接入；LR/engine 的 A/B。
- 候选预设清理/归档。

---

## 4. 这次怎么避免再横跳（操作层）

1. **先写 ADR，再写代码**；没有 owner 的机制不进主链。
2. **先定单位，再定参数**：卡/介质/批次；不要拿一张图当单位。
3. **先定 owner，再定旋钮**：一个特征只给一个旋钮；敏感度矩阵只用来选 owner，不用来叠加。
4. **先定 target，再定求解**：target 是分布/关系/秩；不是“看着像”。
5. **先定判据，再 A/B**：指标/样本/阈值/停止；没有判据的试验不进主链。
6. **一次只改一个 owner**；其他锁死。
7. **freeze 后不再“顺手改”**；改就走 ADR + 版本。
8. **文档单一真源**：`design_v1.0.md` + `decisions.md`；`迭代记录` 只记“改了什么/为什么”，不记“想法”。
9. **不渲染直到 freeze**；探针/合成图除外。
10. **用户只在 freeze 门拍板一次**；中间不再拿“要不要加 X”反复问。

---

## 5. 下一步（不渲染）

1. 把这篇的 10 条不变量 + closed/open 给用户拍板。
2. 建 `decisions.md`（ADR），把 closed 写进去；open 进 P0。
3. 写 `design_v1.0.md` 的目录/契约（不填参数）。
4. 用探针/合成图关 P0 的 1–5；不碰批次。
5. P0 全绿 → freeze `design-v1.0` → 才谈渲染。
