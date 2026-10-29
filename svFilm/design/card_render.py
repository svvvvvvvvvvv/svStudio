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


# ★ 不变量阈值（§58.1 T0 / §63.7）：来自物理与量化下限，**不来自卡的分布**。
#   p95 ≤ 93 —— 大师语料自己的 p95 上沿本来就在 92.9~96.5，93 不严于任何一套；
#   clip < 0.5% —— 可见剪裁的量级；
#   p1 ≥ 1.0 —— 黑端不堵。
INVARIANT_P95_MAX = 93.0
INVARIANT_CLIP_MAX = 0.5
INVARIANT_P1_MIN = 1.0


def safety_for(card) -> Dict[str, float]:
    """引擎的两端边界 = **不变量**（§63.7）。

    卡在这件事上只当参考：它的 p95 说明"这套通常多亮"，用来在匹配页讲代价，
    **不拿来当门的阈值**（拿卡的分布当门 = 把场景当标准）。
    """
    return dict(p95_max=INVARIANT_P95_MAX,
                clip_max=INVARIANT_CLIP_MAX,
                p1_min=INVARIANT_P1_MIN,
                black_max=3.0)


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
