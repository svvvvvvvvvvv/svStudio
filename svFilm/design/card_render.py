# -*- coding: utf-8 -*-
"""把匹配卡（matchcard.MatchCard）接成"能渲的那一份"（设计真源 §48 / §55 / §56 步 5）。

**为什么要这一层**：匹配卡是"一套风格"（介质 + 画像 + 分面 + 样片），它自己不是引擎的 preset；
引擎要的是 preset 名（卷/纸/化学都在那份 JSON 里）。这一层做两件事：
  ① `preset_for(card)`  —— 把卡的**介质**映射到 preset 名（找不到就直接报，不许猜）
  ② `safety_for(card)`  —— 把 `p95_max / black_max` 从**卡的画像区间**算出来（§20/§52）
     不再用卡里写死的 96 / 3

★ 区间怎么取（写清楚，免得以后当靶）：
  卡只有**中位 + IQR**（P25~P75）。这里用 `中位 ± 1.5×IQR` 近似 P10~P90（正态下 1.5·IQR ≈ ±1.28σ ≈ P10/P90）。
  这是**近似**，写在这里就是为了让人知道它不是定义；拿到逐卡的 P10/P90 就换成真的。
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

_PRESET_DIR = r'E:\svStudio\svFilm\svFilm\data\presets'

# 介质（卷/纸）→ preset 名。**反查表**：preset 里的 stock/paper 是唯一真源，
# 这里只做映射；映射不上就抛，不许悄悄用默认卡（那会静默换掉整条链的介质）。
_PRESET_CACHE: Dict[str, Tuple[str, str]] = {}


def _preset_media() -> Dict[str, Tuple[str, str]]:
    """{preset 名: (stock, paper)} —— 从 preset JSON 读，读不到就跳过。"""
    if _PRESET_CACHE:
        return _PRESET_CACHE
    import json
    try:
        for name in sorted(os.listdir(_PRESET_DIR)):
            p = os.path.join(_PRESET_DIR, name, 'preset.json')
            if not os.path.isfile(p):
                continue
            try:
                d = json.load(open(p, encoding='utf-8'))
            except Exception:                              # noqa: BLE001
                continue
            sim = d.get('simulation') or {}
            st, pp = sim.get('film_stock'), sim.get('print_paper')
            if st and pp:
                _PRESET_CACHE[name] = (st, pp)
    except Exception:                                      # noqa: BLE001
        pass
    return _PRESET_CACHE


def preset_for(card) -> str:
    """卡的介质 → preset 名。**映射不上就抛**，不许回退到默认卡。"""
    want = (getattr(card, 'stock', None), getattr(card, 'paper', None))
    if not all(want):
        raise KeyError('这张卡没有介质：%r' % (getattr(card, 'name', card),))
    for name, media in _preset_media().items():
        if media == want:
            return name
    raise KeyError('没有卷/纸为 %s + %s 的 preset（现有：%s）'
                   % (want[0], want[1],
                      '、'.join('%s=%s+%s' % (k, v[0], v[1])
                                for k, v in _preset_media().items())))


def _band(card, key: str, k: float = 1.5) -> Optional[Tuple[float, float]]:
    """卡的某一维 → (下, 上)。中位 ± k×IQR 近似 P10~P90（见文件头）。"""
    prof = getattr(card, 'profile', {}) or {}
    sp = getattr(card, 'spread', {}) or {}
    v = prof.get(key)
    if v is None:
        return None
    q = sp.get(key)
    if q is None or q <= 0:
        return (float(v), float(v))
    return (float(v) - k * float(q), float(v) + k * float(q))


def safety_for(card) -> Dict[str, float]:
    """引擎的两端边界（§20 ②）：**从卡的区间来**，不是卡里写死的 96 / 3。

    · `p95_max` = 卡 p95 区间的**上界**（我们的底片亮部不该越过它）
    · `black_max` = 卡 p5 区间**下界对应的暗部占比口径**——这里没有直方图，
      只能用 clip/p1 的关系给一个**保守值**：拿卡自己的 clip 中位（它说明这套片子
      能容忍多少爆白），黑端仍用历史标准的 3% 起步，并标注"待用卡的 p1 区间替换"。
    """
    out: Dict[str, float] = {}
    b95 = _band(card, 'p95')
    if b95:
        out['p95_max'] = round(float(b95[1]), 2)
    clip = (getattr(card, 'profile', {}) or {}).get('clip')
    if clip is not None:
        # 底片的爆白不该超过这套大师成片的爆白太多：取 2× 它的 clip，最少 0.5%
        out['clip_max'] = round(max(0.5, 2.0 * float(clip)), 2)
    out['black_max'] = float((getattr(card, 'safety', None) or {}).get('black_max', 3.0)) \
        if isinstance(getattr(card, 'safety', None), dict) else 3.0
    return out
