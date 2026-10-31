# -*- coding: utf-8 -*-
"""把匹配卡（matchcard.MatchCard）接成"能渲的那一份"（设计真源 §48 / §55 / §56 步 5）。

**为什么要这一层**：匹配卡是"一套风格"（介质 + 画像 + 分面 + 样片），它自己不是引擎的 preset；
引擎要的是 preset 名（卷/纸/化学都在那份 JSON 里）。这一层做两件事：
  ① `preset_for(card)`  —— 把卡的**介质**映射到 preset 名（找不到就直接报，不许猜）
  ② `safety_for(card)`  —— 把 `p95_max / black_max` 从**卡的画像区间**算出来（§20/§52）
     不再用卡里写死的 96 / 3

★ 区间怎么取（写清楚，免得以后当靶）：
  卡只有**中位 + IQR**（P25~P75）。这里用 `中位 ± 0.95×IQR` 近似 P10~P90（正态：IQR=1.349σ，P10/P90=±1.2816σ=±0.95·IQR）。
  这是**近似**，写在这里就是为了让人知道它不是定义；拿到逐卡的 P10/P90 就换成真的。
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

_PRESET_DIR = r'E:\svStudio\svFilm\svFilm\data\presets'

# 介质（卷/纸）→ preset 名。**反查表**：preset 里的 stock/paper 是唯一真源，
# 这里只做映射；映射不上就抛，不许悄悄用默认卡（那会静默换掉整条链的介质）。
_PRESET_CACHE: Dict[str, Tuple[str, str]] = {}

# ★ 2026-10-31：卡 → preset 的**显式**映射。以前靠"遍历到第一个介质相同的"，
#   而 卡38 与 滨田预设 介质**完全一样**（portra_400 + endura）、amount 却 1.3 vs 0.65；
#   排序恰好让 卡38 先中。一旦 卡38 的 JSON 读不了（实测：被写出 BOM），就会**静默**
#   换成滨田，整批发灰、无人报错。
_CORPUS_PRESET = {'鹿井': '卡38', '滨田': '滨田预设', 'xhs': 'xhs预设'}


def _preset_media() -> Dict[str, Tuple[str, str]]:
    """{preset 名: (stock, paper)} —— 从 preset JSON 读。

    ★ 读不了**必须抛**（以前是 continue 跳过）：坏掉的那张会从介质表里消失，
      `preset_for` 便静默匹配到另一张同介质的卡。
    """
    if _PRESET_CACHE:
        return _PRESET_CACHE
    import json
    bad = []
    for name in sorted(os.listdir(_PRESET_DIR)):
        p = os.path.join(_PRESET_DIR, name, 'preset.json')
        if not os.path.isfile(p):
            continue
        try:
            d = json.load(open(p, encoding='utf-8'))
        except Exception as e:                             # noqa: BLE001
            bad.append('%s: %s' % (name, e))
            continue
        sim = d.get('simulation') or {}
        st, pp = sim.get('film_stock'), sim.get('print_paper')
        if st and pp:
            _PRESET_CACHE[name] = (st, pp)
    if bad:
        raise RuntimeError(
            '预设读不了（**不许静默跳过**，那会悄悄换掉整条链的介质）：\n  '
            + '\n  '.join(bad))
    return _PRESET_CACHE


def preset_for(card) -> str:
    """卡的介质 → preset 名。**映射不上就抛**，不许回退到默认卡。"""
    media = _preset_media()
    corpus = getattr(card, 'corpus', '') or ''
    name = _CORPUS_PRESET.get(corpus)
    if name:
        if name not in media:
            raise KeyError('语料「%s」该用 preset「%s」，但没读到（现有：%s）'
                           % (corpus, name, '、'.join(sorted(media)) or '无'))
        return name
    want = (getattr(card, 'stock', None), getattr(card, 'paper', None))
    if not all(want):
        raise KeyError('这张卡没有介质：%r' % (getattr(card, 'name', card),))
    hits = [n for n, m in media.items() if m == want]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise KeyError('没有卷/纸为 %s + %s 的 preset（现有：%s）'
                       % (want[0], want[1],
                          '、'.join('%s=%s+%s' % (k, v[0], v[1]) for k, v in media.items())))
    raise KeyError('有 %d 个 preset 用同一套介质（%s + %s）：%s ⇒ 卡必须显式带 corpus，'
                   '不许按排序猜' % (len(hits), want[0], want[1], '、'.join(hits)))


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
