# -*- coding: utf-8 -*-
"""卷纸匹配：把「这一批要什么方向」对齐到「卷的固有性格」。

设计要点（分工设计 §8 / §16）：
  · **不追数值**：匹配的是**方向**，不是"哪个卷更接近某批成片"。
  · **卷决定纸**：profile 自带 `target_print` ⇒ 候选是"卷"，纸跟着走。
  · **只用秩与比值**：轴上做批内 z 标准化，避免绝对量跨组比较。
  · 输出**排序 + 理由**，理由必须说清"是哪一维贡献最大"（人挑，不替你挑）。

需求从哪来（都可测）：
    warm   ← 整幅暖冷（warm_cool_b 偏冷 ⇒ 需要更暖的卷）
    dense  ← 意图 denser
    shoulder ← 爆白/天空多 ⇒ 需要高光肩部更宽的卷
    latitude ← 逆光多（脸/背景亮度比低）⇒ 需要宽容度更大的卷

用法：
    from design.match import need_from_batch, rank, explain
    need = need_from_batch(recs, intent)
    for r in rank(reg, need)[:3]:
        print(explain(r))
"""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .stocks import Registry, Stock


@dataclass
class Need:
    """需求向量，每维 −1..+1（正 = 往该方向多要一点）。"""
    warm: float = 0.0       # 要更暖（红过量高）
    dense: float = 0.0      # 要更浓（红过量 + 反差高）
    shoulder: float = 0.0   # 要高光肩更宽（反差低 + dmax 高）
    latitude: float = 0.0   # 要宽容度（反差低）

    def as_dict(self):
        return dict(warm=self.warm, dense=self.dense,
                    shoulder=self.shoulder, latitude=self.latitude)

    def describe(self):
        parts = ['%s%+.2f' % (k, v) for k, v in self.as_dict().items() if abs(v) > 1e-6]
        return '需求：' + (' · '.join(parts) if parts else '无')


def _clamp(v, lo=-1.0, hi=1.0):
    return max(lo, min(hi, v))


def need_from_batch(recs: Dict[str, dict], intent=None) -> Need:
    """从一批「① 引擎片」的测量推出需求向量（只用分布，不用靶）。"""
    if not recs:
        return Need()
    wc = [r.get('warm_cool_b') for r in recs.values() if r.get('warm_cool_b') is not None]  # 仅日志
    clip = [(r.get('img') or {}).get('clip_pct') for r in recs.values()]
    clip = [c for c in clip if c is not None]
    sky = [r.get('sky_share') for r in recs.values() if r.get('sky_share') is not None]
    lr = [r.get('light_ratio') for r in recs.values() if r.get('light_ratio') is not None]
    n = Need()
    # ★ 不用 warm_cool_b 推"要更暖"：那是**我们自己底片的偏差**，不是场景的需求。
    #   拿它挑卷 = 拿缺陷当需求（循环论证，2026-10-28 当场抓到的）。warm 只从意图来。
    if clip:
        n.shoulder = _clamp((st.median(clip) - 0.5) / 4.0, 0.0, 1.0)
    if sky:
        n.shoulder = _clamp(n.shoulder + st.median(sky) / 40.0, 0.0, 1.0)
    if lr:
        # 只在"逆光"时要求更大宽容度；顺光时不许出现"要求更小"（那没有意义）
        n.latitude = _clamp((1.3 - st.median(lr)) / 0.5, 0.0, 1.0)
    if intent is not None:
        n.dense = _clamp(getattr(intent, 'denser', 0.0))
        n.warm += _clamp(getattr(intent, 'warmer', 0.0))
        n.warm = _clamp(n.warm)
    return n


@dataclass
class Hit:
    stock: Stock
    paper: Optional[str]
    score: float
    contrib: Dict[str, float] = field(default_factory=dict)

    def top(self, k=2) -> List[Tuple[str, float]]:
        return sorted(self.contrib.items(), key=lambda kv: -abs(kv[1]))[:k]


def _z(xs: List[float]) -> Tuple[float, float]:
    if len(xs) < 2:
        return 0.0, 1.0
    return st.median(xs), (st.pstdev(xs) or 1.0)


def rank(reg: Registry, need: Need, only_negative: bool = True,
         paper: Optional[str] = None) -> List[Hit]:
    """对候选卷打分并排序。分数越高越"符合这批要的方向"。

    打分方式：轴上做**批内 z 标准化**（只用秩/比值），再与需求点积。
    """
    pool = [s for s in reg.stocks.values() if (s.kind == 'negative' or not only_negative)]
    if paper:
        pool = [s for s in pool if s.target_print == paper]
    pool = [s for s in pool if s.target_print]        # 没有配套纸的先排除（配对不完整）
    if not pool:
        return []

    axes = dict(sepR=[s.sepR for s in pool], gamma=[s.gamma for s in pool],
                dmax=[s.dmax for s in pool], sepB=[s.sepB for s in pool])
    stats = {k: _z(v) for k, v in axes.items()}

    def z(name, v):
        m, sd = stats[name]
        return (v - m) / sd

    hits = []
    for s in pool:
        # 需求 → 轴（方向由手册/器材常识定，符号写在这里，可复查）
        c = {}
        c['warm'] = need.warm * z('sepR', s.sepR)            # 更暖 ⇒ 红过量高
        c['dense'] = need.dense * (0.6 * z('sepR', s.sepR) + 0.4 * z('gamma', s.gamma))
        c['shoulder'] = need.shoulder * (-z('gamma', s.gamma) + 0.5 * z('dmax', s.dmax))
        c['latitude'] = need.latitude * (-z('gamma', s.gamma))
        hits.append(Hit(s, s.target_print, round(sum(c.values()), 4),
                        {k: round(v, 4) for k, v in c.items()}))
    hits.sort(key=lambda h: -h.score)
    return hits


def explain(h: Hit) -> str:
    """一行理由：分数 + 贡献最大的两维。"""
    tops = h.top(2)
    why = ' · '.join('%s%+.2f' % (k, v) for k, v in tops)
    return ('%6.3f  %-24s 纸=%-32s  %s'
            % (h.score, h.stock.name, h.paper or '-', why))


def table(reg: Registry, need: Need, topn: int = 6) -> str:
    hits = rank(reg, need, only_negative=True)
    lines = [need.describe(), '  分数   卷                       配套纸                            主要贡献']
    for h in hits[:topn]:
        lines.append('  ' + explain(h))
    return '\n'.join(lines)
