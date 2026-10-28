# -*- coding: utf-8 -*-
"""卷纸匹配（第二版）：只用**有依据的维度**，输出可复查的排序。

第一版错在哪：拿光谱轴的 z 分数直接点积，维度里混了两类东西——
  · 有依据的（光谱 profile / 粒表 rms / 防光晕三档 / 曲线形状）
  · 没依据的（负片之间的 couplers —— 读 `couplers.toml` 才知道它们**完全一样**，根本不该参与）

这一版的维度表（每条都注出处）：
  ┌ 维度        ┐ 来源                                   ┐
  │ 红过量 sepR  │ profile `log_sensitivity` 的 R−G 差     │ 暖/浓方向
  │ 蓝过量 sepB  │ profile 的 B−G 差                       │ 冷调倾向
  │ 反差 γ       │ profile `density_curves` 的最大斜率      │ 硬/软
  │ 上限 Dmax    │ profile `density_curves` 的最大密度      │ 高光肩
  │ 颗粒 rms     │ 引擎卷表 `grain.toml`（48µm 孔径）      │ 粗细，查表
  │ 防光晕       │ `info.antihalation` 三档                │ strong/weak/no
  │ 纸的反差     │ 纸 profile 的 `density_curves`           │ 组间差异
  └──────────────┘                                        ┘

★ 负片之间 **couplers 一样**（`couplers.toml` 只有 4 种反转片逐卷）⇒ 不进维度。

用法：
    from design.match import need_from_batch, rank, explain, report
"""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .stocks import Paper, Registry, Stock


@dataclass
class Need:
    """需求向量：0..1（单向——不许"要少一点"）。"""
    warm: float = 0.0        # 更暖（红过量高）
    dense: float = 0.0       # 更浓（红过量 + 反差）
    shoulder: float = 0.0    # 高光肩更宽（反差低 + Dmax 高）
    latitude: float = 0.0    # 宽容度（反差低）
    fine: float = 0.0        # 想要**更细**的颗粒（0 = 无偏好）

    def as_dict(self):
        return dict(warm=self.warm, dense=self.dense, shoulder=self.shoulder,
                    latitude=self.latitude, fine=self.fine)

    def describe(self):
        parts = ['%s%+.2f' % (k, v) for k, v in self.as_dict().items() if abs(v) > 1e-6]
        return '需求：' + (' · '.join(parts) if parts else '无（只按场景）')


def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def need_from_batch(recs: Dict[str, dict], intent=None) -> Need:
    """从一批「① 引擎片」的测量推需求。**只用场景侧的量**。

    ⚠ 不用自己出片的颜色偏差（如 warm_cool_b）——那是我们的问题，不是场景的需求；
      拿它挑卷 = 拿缺陷当需求。
    """
    n = Need()
    if not recs:
        return n
    clip = [(r.get('img') or {}).get('clip_pct') for r in recs.values()]
    clip = [c for c in clip if c is not None]
    sky = [r.get('sky_share') for r in recs.values() if r.get('sky_share') is not None]
    lr = [r.get('light_ratio') for r in recs.values() if r.get('light_ratio') is not None]

    if clip or sky:
        s = 0.0
        if clip:
            s += _clamp((st.median(clip) - 0.5) / 4.0)      # 爆白多 ⇒ 要宽肩
        if sky:
            s += _clamp(st.median(sky) / 40.0)              # 天空多 ⇒ 要宽肩
        n.shoulder = _clamp(s)
    if lr:
        # 只在逆光（脸比背景暗）时要更大宽容度；顺光不许出现"要更小"
        n.latitude = _clamp((1.3 - st.median(lr)) / 0.5)
    if intent is not None:
        n.dense = _clamp(getattr(intent, 'denser', 0.0))
        n.warm = _clamp(getattr(intent, 'warmer', 0.0))
        # 软硬：更软 ⇒ 想要更细的颗粒；只当弱偏好
        n.fine = _clamp(getattr(intent, 'softer', 0.0))
    return n


# ---------------------------------------------------------------- 纸的性格

def paper_axes(p: Optional[Paper]) -> Dict[str, float]:
    """纸的反差 / 上限 / 底色暖冷（从它自己的 profile 算）。"""
    if p is None or not getattr(p, 'path', ''):
        return dict(gamma=0.0, dmax=0.0, warm=0.0)
    try:
        import json
        d = json.load(open(p.path, encoding='utf-8'))
        dd = d['data']
        dens = np.asarray(dd['density_curves'], float)
        le = np.asarray(dd['log_exposure'], float)
        g = float(np.gradient(dens, le, axis=0).max(axis=0).mean())
        base = np.asarray(dd['base_density'], float)
        wl = np.asarray(dd['wavelengths'], float)
        if base.ndim == 2:
            base = base.mean(axis=1)
        warm = float(base[wl >= 600].mean() - base[wl <= 480].mean())
        return dict(gamma=g, dmax=float(dens.max()), warm=warm)
    except Exception:
        return dict(gamma=0.0, dmax=0.0, warm=0.0)


@dataclass
class Hit:
    stock: Stock
    paper: Optional[str]
    score: float
    contrib: Dict[str, float] = field(default_factory=dict)
    facts: Dict[str, object] = field(default_factory=dict)

    def top(self, k=2):
        return sorted(self.contrib.items(), key=lambda kv: -abs(kv[1]))[:k]


def _zstat(xs: List[float]) -> Tuple[float, float]:
    if len(xs) < 2:
        return 0.0, 1.0
    return st.median(xs), (st.pstdev(xs) or 1.0)


def _rms_mean(s: Stock) -> float:
    return float(sum(s.rms) / len(s.rms)) if s.rms else 0.0


def rank(reg: Registry, need: Need, only_negative: bool = True,
         paper: Optional[str] = None) -> List[Hit]:
    """候选打分。z 标准化在**候选池内部**做（只用秩/比值，不用绝对靶）。"""
    pool = [s for s in reg.stocks.values() if (s.kind == 'negative' or not only_negative)]
    pool = [s for s in pool if s.target_print]                 # 正片/B&W 没有配套纸，先排除
    if paper:
        pool = [s for s in pool if s.target_print == paper]
    if not pool:
        return []

    stats = dict(sepR=_zstat([s.sepR for s in pool]),
                 gamma=_zstat([s.gamma for s in pool]),
                 dmax=_zstat([s.dmax for s in pool]),
                 rms=_zstat([_rms_mean(s) for s in pool if s.rms] or [0.0]))
    pg = _zstat([paper_axes(reg.papers.get(s.target_print))['gamma'] for s in pool])

    def z(name, v):
        m, sd = stats[name]
        return (v - m) / sd

    def zp(g):
        m, sd = pg
        return (g - m) / sd

    hits = []
    for s in pool:
        px = paper_axes(reg.papers.get(s.target_print))
        c = {}
        c['warm'] = need.warm * z('sepR', s.sepR)
        c['dense'] = need.dense * (0.6 * z('sepR', s.sepR) + 0.4 * z('gamma', s.gamma))
        # 宽肩：卷曲线软 + 卷 Dmax 高 + 纸曲线软（纸也压高光）
        c['shoulder'] = need.shoulder * (-z('gamma', s.gamma) + 0.5 * z('dmax', s.dmax)
                                         - 0.5 * zp(px['gamma']))
        c['latitude'] = need.latitude * (-z('gamma', s.gamma) - 0.3 * zp(px['gamma']))
        if abs(need.fine) > 1e-9 and s.rms:
            # 想要更细 ⇒ rms 小的卷得分高（缺规格的卷不参与这一维，不许当成"最细"）
            c['fine'] = need.fine * (-z('rms', _rms_mean(s)))
        hits.append(Hit(
            stock=s, paper=s.target_print, score=round(sum(c.values()), 4),
            contrib={k: round(v, 4) for k, v in c.items() if abs(v) > 1e-9},
            facts=dict(rms=s.rms or (), anti=s.anti, gamma=round(s.gamma, 3),
                       dmax=round(s.dmax, 3), sepR=round(s.sepR, 2),
                       paper_gamma=round(px['gamma'], 3),
                       missing_rms=not bool(s.rms))))
    hits.sort(key=lambda h: -h.score)
    return hits


def explain(h: Hit) -> str:
    why = ' · '.join('%s%+.2f' % (k, v) for k, v in h.top(2)) or '（无偏好）'
    return ('%6.3f  %-22s rms=%-5s anti=%-6s 纸=%-30s  %s'
            % (h.score, h.stock.name,
               ('%.1f' % _rms_mean(h.stock)) if h.stock.rms else '—',
               h.stock.anti, h.paper or '-', why))


def table(reg: Registry, need: Need, topn: int = 6) -> str:
    hits = rank(reg, need)
    lines = [need.describe(),
             '  分数   卷                     rms   防光晕  配套纸                          主要贡献']
    for h in hits[:topn]:
        lines.append('  ' + explain(h))
    if len(hits) > topn:
        lines.append('  … 共 %d 个候选' % len(hits))
    return '\n'.join(lines)


def report(reg: Registry, need: Need, topn: int = 4) -> str:
    """给人看的一段：排序 + 物理事实 + 该说的警告。"""
    hits = rank(reg, need)
    out = [table(reg, need, topn), '', '  物理事实（查表得来，不是估计）：']
    for h in hits[:topn]:
        f = h.facts
        out.append('    %-22s 颗粒 %-16s 防光晕 %-6s 反差 %.2f Dmax %.2f 红过量 %+.2f 纸反差 %.2f'
                   % (h.stock.name, str(f['rms'] or '—'), f['anti'], f['gamma'],
                      f['dmax'], f['sepR'], f['paper_gamma']))
    out.append('')
    out.append('  ⚠ 排序只表示"方向对齐"，不表示"更好"。选哪卷是审美判断。')
    out.append('  ⚠ 负片之间 couplers 相同（couplers.toml 只有 4 种反转片逐卷）⇒ 不参与排序。')
    out.append('  ⚠ 卷决定纸（profile 自带 target_print）⇒ 换卷=换纸，白点/反差会一起变。')
    miss = [h.stock.name for h in hits if h.facts.get('missing_rms')]
    if miss:
        out.append('  ⚠ 缺颗粒规格（引擎 grain.toml 里没有）⇒ 不参与"粗细"这一维：%s'
                   % ', '.join(miss))
    return '\n'.join(out)
