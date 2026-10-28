# -*- coding: utf-8 -*-
"""卡层的匹配：在**现有卡**里挑，和"哪些卷值得做新卡"是两件事。

对外只有"卡"（见 `cards.py`）。这个模块给两个东西：

  `rank_cards(tb, need)`   → 现有卡之间的排序（**能立刻用**）
  `rank_stocks(reg, need)` → 卷之间的排序（**值得做新卡的参考**，不是能选的）

两者共用同一套需求向量与同一条"方向对齐"打分，只是候选池不同。

★ 打分只用有依据的维度（每条注出处）：
    sepR / sepB / gamma / dmax ← profile（`log_sensitivity` / `density_curves`）
    rms                        ← 引擎 `grain.toml`（卡上若有自己的档位则用卡的）
    anti                       ← `info.antihalation` 三档
    纸反差                      ← 纸 profile 的 `density_curves`
  ✗ 负片之间的 couplers（`couplers.toml` 只有 4 种反转片逐卷 ⇒ 负片全同）
"""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .cards import Card, card_axes
from .match import Need, _zstat, paper_axes
from .stocks import Registry, Stock


@dataclass
class CardHit:
    card: Card
    score: float
    contrib: Dict[str, float] = field(default_factory=dict)
    facts: Dict[str, object] = field(default_factory=dict)

    def top(self, k=2):
        return sorted(self.contrib.items(), key=lambda kv: -abs(kv[1]))[:k]


def rank_cards(tb: Dict[str, Card], reg: Registry, need: Need) -> List[CardHit]:
    """现有卡排序。候选少（现在 3 张），z 标准化会很粗 —— 这是实情，不粉饰。"""
    pool = [c for c in tb.values() if c.stock in reg.stocks]
    if not pool:
        return []
    axes = {c.name: card_axes(c, reg) for c in pool}
    stats = dict(
        sepR=_zstat([float(axes[c.name].get('sepR') or 0) for c in pool]),
        gamma=_zstat([float(axes[c.name].get('gamma') or 0) for c in pool]),
        dmax=_zstat([float(axes[c.name].get('dmax') or 0) for c in pool]),
        rms=_zstat([float(axes[c.name].get('rms') or 0) for c in pool]),
        pgamma=_zstat([paper_axes(reg.papers.get(c.paper))['gamma'] for c in pool]),
        amount=_zstat([float(axes[c.name].get('amount') or 0) for c in pool]),
        format=_zstat([float(axes[c.name].get('format_mm') or 0) for c in pool]),
    )

    def z(name, v):
        m, sd = stats[name]
        return (v - m) / sd

    hits = []
    for c in pool:
        a = axes[c.name]
        c_ = {}
        c_['warm'] = need.warm * z('sepR', float(a.get('sepR') or 0))
        c_['dense'] = need.dense * (0.6 * z('sepR', float(a.get('sepR') or 0))
                                    + 0.4 * z('gamma', float(a.get('gamma') or 0)))
        c_['shoulder'] = need.shoulder * (-z('gamma', float(a.get('gamma') or 0))
                                          + 0.5 * z('dmax', float(a.get('dmax') or 0))
                                          - 0.5 * z('pgamma', paper_axes(reg.papers.get(c.paper))['gamma']))
        c_['latitude'] = need.latitude * (-z('gamma', float(a.get('gamma') or 0))
                                          - 0.3 * z('pgamma', paper_axes(reg.papers.get(c.paper))['gamma']))
        if abs(need.fine) > 1e-9:
            if a.get('rms'):
                c_['fine'] = need.fine * (-z('rms', float(a['rms'])))
            # 画幅大 ⇒ 同一 rms 的相对颗粒更细（手册：36→70 时 σ_px ×0.514）
            if a.get('format_mm'):
                c_['format'] = 0.2 * need.fine * z('format', float(a['format_mm']))
        if abs(need.dense) > 1e-9 and a.get('amount'):
            # 卡自己的彩度总量也进"浓淡"（否则同卷同纸的两张卡永远分不开）
            c_['amount'] = 0.3 * need.dense * z('amount', float(a['amount']))
        hits.append(CardHit(card=c, score=round(sum(c_.values()), 4),
                            contrib={k: round(v, 4) for k, v in c_.items() if abs(v) > 1e-9},
                            facts=dict(rms=a.get('rms'), anti=a.get('anti'),
                                       gamma=a.get('gamma'), dmax=a.get('dmax'),
                                       sepR=a.get('sepR'),
                                       format_mm=a.get('format_mm'),
                                       amount=a.get('amount'))))
    hits.sort(key=lambda h: -h.score)
    return hits


def explain_card(h: CardHit) -> str:
    why = ' · '.join('%s%+.2f' % (k, v) for k, v in h.top(2)) or '（无偏好）'
    f = h.facts
    rms = f.get('rms')
    return ('%6.3f  %-10s %-22s rms=%-5s 画幅=%-4s 柔光之外 彩度=%-5s %s'
            % (h.score, h.card.name, h.card.stock,
               ('%.1f' % rms) if isinstance(rms, float) else '—',
               f.get('format_mm') or '—', f.get('amount') or '—', why))


def table_cards(tb: Dict[str, Card], reg: Registry, need: Need) -> str:
    hits = rank_cards(tb, reg, need)
    lines = [need.describe(),
             '  分数   卡          卷                        rms   画幅  彩度   主要贡献']
    for h in hits:
        lines.append('  ' + explain_card(h))
    if len(hits) < 4:
        lines.append('  ! 候选只有 %d 张卡 ⇒ z 标准化很粗，排序只当参考' % len(hits))
    return '\n'.join(lines)
