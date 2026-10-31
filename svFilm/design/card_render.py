# -*- coding: utf-8 -*-
"""把匹配卡（matchcard.MatchCard）接成"能渲的那一份"（设计真源 §48 / §55 / §56 步 5）。

**为什么要这一层**：匹配卡是"一套风格"（介质 + 画像 + 分面 + 样片），它自己不是引擎参数；
引擎要的是一份**介质块**（卷/纸/化学/扫描端点…）。这一层做两件事：
  ① `medium_of(card)`   —— 取**这个目录自己的**介质块（`design/medium.py`；读不到就报）
  ② `reference_for(card)` —— 卡的数值**只报不判**（匹配页/账里用）

★ 2026-10-31：**不再给引擎 p95_max/black_max**。引擎固定 PE 渲，只守死白/死黑；
  拿卡的分布当门 = 把场景当标准，已删（有 git 历史）。

★ 2026-10-31：**"预设"这个概念已删**（没有可选集合、没有按名字查参数）；
  每个目录自带一份介质块，引擎直接吃 dict。

★ 区间怎么取（写清楚，免得以后当靶）：
  卡只有**中位 + IQR**（P25~P75）。这里用 `中位 ± 0.95×IQR` 近似 P10~P90（正态：IQR=1.349σ，P10/P90=±1.2816σ=±0.95·IQR）。
  这是**近似**，写在这里就是为了让人知道它不是定义；拿到逐卡的 P10/P90 就换成真的。
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple


def medium_of(card) -> dict:
    """卡的**介质块**（卷/纸/WB/amount/化学…）。没有就抛。

    ★ 2026-10-31：这一层原来叫 `preset_for`——把卡映射到一个"预设名"，
      再由引擎按名字去查参数。现在没有"预设"这个可选集合了：每个目录自带一份介质，
      引擎直接吃这个 dict（`svFilm.presets._params_for` 已支持）。
    """
    from .medium import medium_of as _medium_of
    return _medium_of(card)


def _band(card, key: str, k: float = 0.95) -> Optional[Tuple[float, float]]:
    """卡的某一维 → (下, 上)。中位 ± k×IQR 近似 P10~P90（见文件头）。**只当参考用。**"""
    prof = getattr(card, 'profile', {}) or {}
    sp = getattr(card, 'spread', {}) or {}
    v = prof.get(key)
    if v is None:
        return None
    q = sp.get(key)
    if q is None or q <= 0:
        return (float(v), float(v))
    return (float(v) - k * float(q), float(v) + k * float(q))


def reference_for(card) -> Dict[str, object]:
    """卡的数值 → **只报不判**的参考（匹配页/账里用）。"""
    out: Dict[str, object] = {}
    for key in ('p95', 'p50', 'clip'):
        b = _band(card, key)
        if b:
            out[key + '_band'] = (round(b[0], 1), round(b[1], 1))
    v = (getattr(card, 'profile', {}) or {}).get('p95')
    if v is not None:
        out['p95'] = round(float(v), 1)
    return out
