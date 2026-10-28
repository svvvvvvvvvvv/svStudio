# -*- coding: utf-8 -*-
"""卷 / 纸：从引擎 profile 直接读出「性格」，不靠渲、不靠拟合大师。

设计出处：`E:\\ds\\photo\\分工设计_引擎与LR_1029.md` §8（卷子怎么选）。

三条硬事实（读源码确认）：
  1. 每个卷 profile 自带 `info.target_print` ⇒ **卷决定了纸**，不是自由组合。
  2. `base_density` 直接加在光谱密度上（`spektrafilm/develop.py`）⇒ 胶片自带的底色。
  3. `log_sensitivity` 决定"卷+纸"配对的中性滤片（`color_filters.py`）⇒ 换卷会连带改滤片值。

用法：
    from design.stocks import registry
    reg = registry()
    for s in reg.sorted_by('sepR'): print(s.name, s.sepR)
    print(reg.groups())          # 三组阵营
"""
from __future__ import annotations

import glob
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

_VENDOR = r'E:\svStudio\svFilm\_tools\spektrafilm\src'
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)
try:
    from spektrafilm.data import presets_loader as _PL
    GRAIN_TABLE = {k: v.get('rms_granularity') for k, v in _PL.read_grain_presets().items()}
    COUPLER_TABLE = _PL.read_coupler_presets()
except Exception:                                    # 引擎不在时也不崩
    GRAIN_TABLE, COUPLER_TABLE = {}, {}

PROFILE_DIRS = [
    r'E:\svStudio\svFilm\_tools\spektrafilm\src\spektrafilm\data\profiles',
    r'E:\spektrafilm\src\spektrafilm\data\profiles',
]


@dataclass
class Stock:
    name: str                    # info.stock
    kind: str                    # negative / positive
    anti: str                    # strong / weak / no
    use: str                     # still / cine
    target_print: Optional[str]
    speed: float                 # 光谱灵敏度均值（越大越快）
    sepR: float                  # R−G 灵敏度差（红过量）
    sepB: float                  # B−G 灵敏度差（蓝过量）
    gamma: float                 # 密度曲线最大斜率均值（正片无意义）
    dmax: float
    rms: tuple = ()              # 引擎卷表 grain.toml 的 rms_granularity（48µm 孔径）
    couplers: dict = field(default_factory=dict)   # 引擎卷表 couplers.toml 的 γ
    coupler_class: str = ''                        # 正片=per-stock；负片=color.negative 类默认
    path: str = ''

    def axes(self) -> Dict[str, float]:
        a = dict(speed=self.speed, sepR=self.sepR, sepB=self.sepB,
                 gamma=self.gamma, dmax=self.dmax)
        if self.rms:
            a['rms'] = float(sum(self.rms) / len(self.rms))
        return a

    def __str__(self):
        rms = ('%.1f' % (sum(self.rms) / len(self.rms))) if self.rms else '—'
        return ('%-24s %-9s anti=%-6s rms=%-5s sepR=%+.2f sepB=%+.2f γ=%.2f 纸=%s'
                % (self.name, self.kind, self.anti, rms, self.sepR, self.sepB,
                   self.gamma, self.target_print))


@dataclass
class Paper:
    name: str
    kind: str
    path: str = ''


def _walk_profiles():
    seen = {}
    for d in PROFILE_DIRS:
        if not os.path.isdir(d):
            continue
        for f in sorted(glob.glob(os.path.join(d, '*.json'))):
            seen.setdefault(os.path.basename(f), f)
    return seen



def _resolve_couplers(st, d):
    """couplers 表只对**正片**逐卷；负片全用同一个类默认。

    读 `data/presets/couplers.toml` 的事实（2026-10-29）：
      条目只有 5 个：defaults + 4 种反转片（provia/velvia/ektachrome100/kodachrome64）
      ⇒ **负片之间 couplers 完全一样** ⇒ 它不能用来区分负片。
    """
    per = dict(COUPLER_TABLE.get(st.name) or {})
    if per:
        return per, 'per-stock'
    info = d.get('info') or {}
    kind = info.get('type') or 'negative'
    use = info.get('use') or 'still'
    classname = 'color.%s' % kind
    if kind == 'negative' and use == 'cine':
        illum = (info.get('reference_illuminant') or '').upper()
        sub = 'tungsten' if 'T' == illum[:1] and illum.startswith('T') else 'daylight'
        cand = 'color.negative.cine.%s' % sub
        if cand in (COUPLER_TABLE.get('defaults') or {}):
            return dict(COUPLER_TABLE['defaults'][cand] or {}), cand
    cls = (COUPLER_TABLE.get('defaults') or {}).get(classname) or {}
    return dict(cls), classname


def _fingerprint(d: dict) -> Optional[Stock]:
    info = d.get('info') or {}
    dd = d.get('data') or {}
    if not info.get('stock'):
        return None
    if info.get('stage') != 'filming':
        return None
    try:
        sens = np.asarray(dd['log_sensitivity'], float)
        dens = np.asarray(dd['density_curves'], float)
        le = np.asarray(dd['log_exposure'], float)
    except Exception:
        return None
    sp = sens.mean(axis=0) if sens.ndim == 2 and sens.shape[1] == 3 else np.zeros(3)
    g = np.gradient(dens, le, axis=0).max(axis=0) if dens.ndim == 2 else np.zeros(3)
    return Stock(
        name=info['stock'], kind=info.get('type'), anti=info.get('antihalation', '?'),
        use=info.get('use', '?'), target_print=info.get('target_print'),
        speed=float(sp.mean()), sepR=float(sp[0] - sp[1]), sepB=float(sp[2] - sp[1]),
        gamma=float(g.mean()), dmax=float(dens.max()))


@dataclass
class Registry:
    stocks: Dict[str, Stock] = field(default_factory=dict)
    papers: Dict[str, Paper] = field(default_factory=dict)

    # ---- 查询 ----
    def sorted_by(self, axis: str, only_negative: bool = True) -> List[Stock]:
        xs = [s for s in self.stocks.values()
              if (s.kind == 'negative' or not only_negative)]
        return sorted(xs, key=lambda s: getattr(s, axis))

    def groups(self) -> Dict[str, List[Stock]]:
        """按配套纸分组 —— 这就是"卷决定纸"。"""
        g: Dict[str, List[Stock]] = {}
        for s in self.stocks.values():
            g.setdefault(s.target_print or '(未配纸)', []).append(s)
        for k in g:
            g[k].sort(key=lambda s: s.name)
        return g

    def papers_used(self) -> List[str]:
        return sorted({s.target_print for s in self.stocks.values() if s.target_print})

    def filter(self, negative=True, anti=None, sepR_max=None, gamma_min=None):
        out = []
        for s in self.stocks.values():
            if negative and s.kind != 'negative':
                continue
            if anti and s.anti != anti:
                continue
            if sepR_max is not None and s.sepR > sepR_max:
                continue
            if gamma_min is not None and s.gamma < gamma_min:
                continue
            out.append(s)
        return sorted(out, key=lambda s: s.sepR)


def registry() -> Registry:
    reg = Registry()
    for base, path in _walk_profiles().items():
        try:
            d = json.load(open(path, encoding='utf-8'))
        except Exception:
            continue
        info = d.get('info') or {}
        st = _fingerprint(d)
        if st:
            st.path = path
            st.rms = tuple(GRAIN_TABLE.get(st.name) or ())
            st.couplers, st.coupler_class = _resolve_couplers(st, d)
            reg.stocks[st.name] = st
        elif info.get('stage') == 'printing' and info.get('stock'):
            reg.papers[info['stock']] = Paper(info['stock'], info.get('type', '?'), path)
    return reg


if __name__ == '__main__':
    reg = registry()
    print('卷 %d / 纸 %d' % (len(reg.stocks), len(reg.papers)))
    for paper, xs in reg.groups().items():
        print('\n== 配 %s ==' % paper)
        for s in xs:
            print('  ' + str(s))
