# analysis/medium — 卷纸测量（look book）

测量方法见 [`../../design/卷纸测量.md`](../../design/卷纸测量.md)。

## 它做什么

用**作者引擎**在**同一张作者测试图**上渲 11 个 `target_print` 对，量引擎的 `face L/C/h`、`leaf a/b`、`contrast`；再用 4 组纸/卷交叉配对分清“卷”和“纸”。

**不做**：不从大师 JPG 统计反推卷纸；不用旧预设/卡38/xhs/old amount；不写自动匹配结论。

## 运行

```bash
python analysis/medium/lookbook.py          # 11 对 look book -> out/lookbook
python analysis/medium/measure.py           # 测量 + 4 组交叉配对 -> out/lookbook_metrics.json
python analysis/medium/assign.py            # 64 卡视觉配方页 -> out/all_cards_assignment.html
python analysis/medium/apply_assignments.py --dry-run
python analysis/medium/apply_assignments.py --apply   # 先备份，再写 stock/paper
python analysis/medium/wb_review.py                   # 卡级 WB 复核
python analysis/medium/amount_ramp.py                 # 9 配方 amount ramp
python analysis/medium/pe_tables.py                  # 64 卡 PE 表（默认 1080 + 0.0625..4）
python analysis/medium/pe_selftest.py                 # PE 表自检
python analysis/medium/pe_batch_check.py              # 4 张样本逐图验算
python analysis/medium/pe_full_check.py               # 19 张 xhs 全量验算
python analysis/medium/card_samples_review.py         # 64 卡多样片复核页
python analysis/medium/wb_amount_review.py            # WB/amount 卡级复核页
python analysis/medium/validate_engine.py             # vendor 确定性/基线对照
```

可选：

```bash
python analysis/medium/lookbook.py --papers all   # 11×6 全组合
python analysis/medium/lookbook.py --limit 2      # 冒烟
```

## 输入/输出

- 输入图：`_tools/spektrafilm/img/test/portrait_leaves_32bit_linear_prophoto_rgb.tif`
- 引擎：`_tools/spektrafilm`（vendor 0.3.3）
- 输出：`analysis/medium/out/`
  - `lookbook/*.png`
  - `lookbook/lookbook.json`
  - `cross/*.png`
  - `lookbook_metrics.json`
  - `lookbook_sheet.png` / `lookbook_faces.png`
  - `all_cards_assignment.html` / `card_recipes.csv`
  - `apply_report.json`；写卡前备份在 `out/backup_cards_before_*`
  - `wb_review.json` / `amount_ramp.json` / `amount_ramp.md`
  - `pe_tables/*.json` / `pe_selftest.json` / `pe_batch_check.json` / `pe_full_check.json`
  - 生产表在 `analysis/corpus/kurai/pe_tables/`（进版本；`out/` 只是工作副本）
  - `card_samples_review.html` / `.json`
  - `wb_amount_review.html`
  - `baseline_vendor.json`

## 口径（必须保持）

- 同一张测试图、同一 auto exposure / PE、grain/halation 开；
- sRGB D65 Lab；固定 face/leaf/whole 区域；
- 同卷换纸、同纸换卷；结论写明卷还是纸；
- 只写测量值（face C/h、leaf a/b、contrast），不写没有数的形容词；
- 低置信只认族/配方，不硬给单支。
- PE 表按卡加载：`chart_calib.load_card_table(card_name)`；**不回退旧全局表**。
- PE 表网格必须覆盖 target 所需 PE；旧 0.25–4 对高亮目标不够，新默认 0.0625–4；当前只有 008 已重建，其余 63 张待重建。
- `hit_bound` 不渲；表外 PE 不再外推。
- 多样片页只标极端曝光/内容跨度，不自动拆卡；风格是否一致要人看。

## 迭代

换引擎/换 profile → 重跑全部；更新 `lookbook_metrics.json` 和 HTML，不混版本。
