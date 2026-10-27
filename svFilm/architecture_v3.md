# 架构 v3：一个测量、三层职责、旧代码退出主链

> 目标：把“卡 target / 引擎介质 / LR placement+look”三条线彻底切开；  
> 只保留一个测量口径；旧 probe / 旧 person / 旧 skin / 旧 finish 全部退出主链。

---

## 1. 三层职责（只这三层）

```
① 卡 target（measure_v2）
   脸：L/C/h（硬门/落点）
   前景：p5/p25/p50/p75/p95（排除背景/服装）
   背景：p5..p95（只存，不进端点靶）
   颜色：shadow/mid/high a/b、skin h/C
   介质：grain/halation/scan/paper/format

② 引擎 sfFilm（介质）
   卷/纸/化学/颗粒/光晕/扫描/画幅
   print_exposure 固定；WB 固定；amount 固定；scanner black/white 固定
   不做 placement，不做肤色，不做曲线

③ LR（placement + look）
   Basic 六旋钮：Exposure→脸，Whites→前景 p99，Highlights→前景 p95，
                 Shadows→前景 p5，Blacks→前景 p1，Contrast→前景 p25–p75
   Tone Curve：形状（≤16 点，端点锁）
   HSL：橙/黄（肤色）、绿/蓝（背景）
   Split Toning / Color Grading：shadow/high a/b
   Camera Calibration：蓝原色
   Lens/Transform
```

**边界测试**：改“胶片物理响应”归引擎；改“落点/色调”归 LR。

---

## 2. 一个测量：measure_v2

旧的三套皮肤（`qa15` / `_probe_clip` / `skin_v2`）和两套 fg/bg 必须收成一套。

```
measure_v2(path) -> {
  face:   {L,C,h,n, mask},
  fg:     {p5,p25,p50,p75,p95},
  bg:     {p5,p25,p50,p75,p95},
  fg_p99,  fg_p1,
  color:  {shadow_a/b, mid_a/b, high_a/b},
  face_mask, fg_mask, bg_mask
}
```

- 皮肤：`skin_v2` 的 C/L/h（唯一）。
- 脸 mask：`skin_v2` 自己的 YuNet 脸，不再依赖 `svFilm.person`。
- fg/bg：人物 mask（脸+身体+服装），背景（天空/地面/两侧）；不再用“中央 20–80%”。
- 色彩：fg 的 shadow/mid/high a/b。
- 端点：fg 的 p99/p1。
- **同一分辨率域**：卡和渲染都在 1080 sRGB JPEG 上量；引擎 TIF 先转 1080 JPEG 再量。

---

## 3. 旧代码退出主链（删除/替换）

| 旧 | 处理 | 原因 |
|---|---|---|
| `svFilm.person` | 删除 | 旧人像分割；`skin_v2` 改用自带脸 mask |
| `measure_pilot3` 的 `P` | 替换 | 不再 import `svFilm.person` |
| `_probe_clip.py` | 删除 | 旧 probe；`measure_v2` 取代 |
| `skin_zone.py` | 删除 | 旧 helper；`measure_v2` 自带 load/det |
| `qa15` | 删除/归档 | 旧皮肤口径 |
| `directories.json` 的旧 skin | 只作历史 | `face_lo/hi` 改由 `measure_v2` 的 16 张分布 |
| `card_engine_38` / `card_lr_38` | 重生成 | 旧靶/旧口径 |
| `design_v2` 旧跑批 | 归档 | 旧口径 |
| `finish.json`/`bands`/`calibration` | 已删 | 旧 tone 靶 |
| `basic.py`/`look_stage.py`/`cal_basic`/`basic_gain` | 已删 | 旧机制 |
| `lr_apply`/`lr_measure`/`_add_batch` | 已删 | 无引用 |
| `svFilm.person_pe` 残留 | 已删 | 旧机制 |

---

## 4. 新的文件布局

```
card_target/
  measure_v2.py        唯一测量（skin_v2 + fg/bg + color + endpoints）
  card_target_v3.py    从 measure_v2 生成卡 target
engine/
  svFilm/              介质 only（preset.json 只留 medium）
lr/
  run_v3.py            六旋钮 → curve → HSL → SplitToning → Calibration
data/
  卡38/preset.json     只留介质
  card38_target_v3.json
```

---

## 5. 求解顺序（v3）

1. **measure_v2**：脸/前景/背景/颜色/端点。
2. **Exposure**：脸 L → 卡的脸带（硬门）。
3. **Whites/Blacks**：前景 p99/p1（背景不参与）。
4. **Highlights/Shadows**：前景 p95/p5（不压脸）。
5. **Contrast**：前景 p25–p75。
6. **HSL**：橙/黄按 skin h/C；绿/蓝按 bg。
7. **Curve**：形状残差（≤16 点，端点锁）。
8. **Split Toning / CG**：shadow/high a/b。
9. **Calibration**：蓝原色。
10. **2–3 轮**；每轮只动残差最大且 owner 对应的一个；其余锁死。

---

## 6. 职责表（最终）

| 特征 | 唯一 owner | 层 |
|---|---|---|
| 脸 L | Exposure | LR |
| 前景 p99 | Whites | LR |
| 前景 p95 | Highlights | LR |
| 前景 p5 | Shadows | LR |
| 前景 p1 | Blacks | LR |
| 前景 p25–p75 | Contrast | LR |
| p5–p95 形状 | Tone Curve | LR |
| 肤色 h/C | HSL 橙/黄 | LR |
| 背景绿/蓝 | HSL 绿/蓝 | LR |
| 阴影/高光染色 | Split Toning / CG | LR |
| 整体色偏 | Camera Calibration | LR |
| 卷/纸/化学/颗粒/光晕/扫描/画幅 | 介质 profile | engine |
| 印相曝光/WB/amount/黑白端 | 介质固定 | engine |

---

## 7. 实施顺序

1. 写 `measure_v2`（skin_v2 自带脸 mask；fg/bg 人物 mask；1080 域）。
2. 写 `card_target_v3`（16 张分布；不再用旧 `directories.json` skin）。
3. 写 `run_v3`（六旋钮/曲线/HSL/SplitToning/Calibration；全走 measure_v2）。
4. 删 `_probe_clip`/`skin_zone`/`measure_pilot3` 的 `P`/`qa15`/旧输出。
5. 3 张跑通 → A/B → 再谈整批。
