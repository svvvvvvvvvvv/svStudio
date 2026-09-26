# -*- coding: utf-8 -*-
r"""**语义区域**（不是色相带）—— 09-24 方法论自查那条的落地。

## 为什么要有这个文件
09-24 自查发现：L3 混色原本按**色相带**（10 个）分区调色，这跟 L4 那次"用色相窗当人脸"
是**同一个病根** —— **用一个"能算的量"（色相）代替一个"需要判断的量"（这是什么）**。

> **LR 的混色器也按色相带 —— 但那是给人用的。** 人看着画面知道「这块绿是树叶还是荧光灯」，
> 再决定怎么调。我们自动用色相带，少了这一步 ⇒ "绿色的都降饱和"必然误伤。
> 实测证据：各图的色相占比差 **10~900 倍**（蓝带有的图 0.0%、有的 8.3%）。

外部（GitHub）也一致：`rodrigorcz/skin-color-correction` 用 **MediaPipe 人脸分割**、
`SonyResearch/skin-tone-extraction` **必须给 mask** —— **都按语义区域，没有一家按色相带**。

## 四个区域
| 区域 | 怎么来 | 可信度 |
|---|---|---|
| `person` | `face.py` 的 person 掩膜（MediaPipe 分割） | ★ 模型给的，**硬** |
| `sky` | 启发式：亮 + 蓝青色相 + 偏画面上部 | ⚠ 软，会误判（比如亮蓝色的衣服） |
| `veg` | 启发式：绿色相 + 中高彩度 | ⚠ 软，会误判（比如绿色的墙） |
| `rest` | 1 − 上面三个 | — |

★ **诚实的定位**：`person` 是可信的（模型），`sky`/`veg` 是启发式（近似）。
  用途上分两档：
  - **"人 vs 环境"** —— 用 `person`，**可靠** ⇒ 这条当主力
  - **"天空 / 植被 / 其余"** —— 近似 ⇒ 只用来做**轻度**的差异化，别把结论建在它上面
"""
from __future__ import annotations

import numpy as np

from . import color


def _ramp(x, lo, hi):
    if hi == lo:
        return np.zeros_like(x, dtype=np.float64)
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def masks(disp, cfg=None, parsed=None):
    """算出四个**软**掩膜（0~1，和 disp 同尺寸）。

    `parsed`：**已经**由调用方算好的一次 `face.parse(...)` 的结果。给了就直接拿它的
      `person`，**不再调一遍分割模型**。
      ★★★ 为什么要这个参数（09-26）：① `grade.apply` 里 `region.weights` 和 L4 各要一份掩膜，
      同一份像素调两遍 = 白付一次分割（实测 273 ms/次）；② 更要紧的是**喂哪张图** ——
      `pipeline` 老路专门在**解码后**算掩膜（链尾发白 ⇒ 模型认不出脸），新路若在这里现算，
      拿到的是**引擎出图之后**的成片 ⇒ 又撞上那个病。所以掩膜由 pipeline 在解码后算一次、传下来。

    @returns {dict} dict(person=…, sky=…, veg=…, rest=…, src='…')
    """
    d = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    H, W = d.shape[:2]
    person = None
    src = 'heuristic'
    if parsed is not None:                            # ① 调用方给的（解码后那张图算的）
        mk = (parsed or {}).get('masks') or {}
        _pp = mk.get('person')
        # ⚠ 尺寸必须对得上才认（掩膜是在另一张同尺寸的图上算的；换过渲染尺寸就不认）
        if _pp is not None and tuple(np.shape(_pp)[:2]) == (H, W):
            person = np.clip(np.asarray(_pp, np.float64), 0.0, 1.0)
            src = 'given'
    if person is None:                                # ② 没有就直接算（单独调本函数时）
        try:
            from . import face as _F
            r = _F.parse(d)
            mk = (r or {}).get('masks') or {}
            if mk.get('person') is not None:
                person = np.clip(np.asarray(mk['person'], np.float64), 0.0, 1.0)
                src = 'face'
        except Exception:                                              # noqa: BLE001
            person = None
    if person is None:
        person = np.zeros((H, W), np.float64)

    lab = color.to_lab(d)
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    C = np.sqrt(a * a + b * b)
    hue = np.degrees(np.arctan2(b, a)) % 360.0

    # ② 天空 / 水面：亮 + 蓝青色相 + 偏上部（三样都满足才给高权重）
    yy = np.linspace(0.0, 1.0, H)[:, None] * np.ones((1, W))           # 0=顶 1=底
    up = 1.0 - yy                                                       # 越靠上越像天
    hue_blue = _ramp(np.abs(((hue - 210.0 + 180.0) % 360.0) - 180.0), 60.0, 20.0) \
        if False else (1.0 - _ramp(np.abs(((hue - 210.0 + 180.0) % 360.0) - 180.0),
                                   20.0, 70.0))
    sky = hue_blue * _ramp(L, 55.0, 72.0) * _ramp(C, 3.0, 14.0) * (0.35 + 0.65 * up)
    sky = np.clip(sky * (1.0 - person), 0.0, 1.0)

    # ③ 植被：绿色相 + 中高彩度
    hue_green = 1.0 - _ramp(np.abs(((hue - 110.0 + 180.0) % 360.0) - 180.0), 25.0, 65.0)
    veg = hue_green * _ramp(C, 8.0, 20.0)
    veg = np.clip(veg * (1.0 - person) * (1.0 - sky), 0.0, 1.0)

    rest = np.clip(1.0 - person - sky - veg, 0.0, 1.0)
    return dict(person=person, sky=sky, veg=veg, rest=rest, src=src)


def weights(disp, cfg=None, grow_person=None, parsed=None):
    """区域 → **加权系数**：把"色相带增益"按区域缩放。

    默认（`config.GRADE_REGION_SCOPE`）：
      · `'env'`（当前）：**环境增益只作用在"非人"区域** ——
        人像区不套用环境的色相带增益（人像归 L4 肤色管）；
      · `'all'`：不分区（老行为）。

    `parsed`：调用方已经算好的 `face.parse(...)` 结果（见 `masks` 的说明）—— 传了就复用，
      不再多跑一遍分割模型。
    """
    m = masks(disp, cfg, parsed=parsed)
    w_person = m['person']
    if grow_person is None:
        grow_person = 1.0
    w_person = np.clip(w_person * float(grow_person), 0.0, 1.0)
    env = np.clip(1.0 - w_person, 0.0, 1.0)           # ★ 环境 = 非人
    inv = dict(m)
    inv['env_scale'] = env
    return inv
