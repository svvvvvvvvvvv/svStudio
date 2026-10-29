# -*- coding: utf-8 -*-
"""卡：这套系统里**唯一被匹配、被选用、被渲染**的单位。

一个「卡」= 一卷 + 一张纸 + 一组质感参数 + 风格档。
它对外是卡；对内落成引擎侧的一个配置文件（实现细节，不外露）。

为什么要有这一层（2026-10-29 修正）：
  · 卷有 22 个，但**能渲的不等于能选的**——每个卡都是有人定过、校验过的一个组合；
  · 匹配的候选池应该是**卡**，不是"所有卷的笛卡尔积"；
  · 早先我把它叫"预设"，那是引擎侧目录的叫法，**不是这套系统的单位**；本系统只有"卡"。

卡绑定的东西（都从已有卡的实现里读出）：
    stock / paper          ← 卷+纸（决定光谱、白点、反差）
    grain.rms_granularity  ← 颗粒档位（可选，缺省按卷表）
    halation.strength      ← 光晕档位
    film_format_mm         ← 画幅
    diffusion              ← 柔光档
    couplers.amount        ← 彩度总量
    export                 ← 交付规格

用法：
    from design.cards import cards, card_axes
    for c in cards().values(): print(c)
"""
from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .stocks import Registry, Stock, registry as _registry

# 卡的实现放在引擎侧 `data/presets/` 目录里（这是"卡怎么落地"，不是"卡是什么"）
CARD_DIRS = [r'E:\svStudio\svFilm\svFilm\data\presets']


@dataclass
class Card:
    name: str                       # 卡名（对外）
    stock: str                      # 卷
    paper: str                      # 纸
    texture: Dict[str, object] = field(default_factory=dict)   # 质感档位
    style: Dict[str, object] = field(default_factory=dict)     # 风格档（柔光/画幅等）
    export: Dict[str, object] = field(default_factory=dict)    # 交付规格
    impl: str = ''                  # 落成哪个文件（实现细节）
    note: str = ''
    # 余量设定（T0）。引擎默认 p95_max=96（宽）；我们更严，但**这是卡的选择**
    safety: Dict[str, float] = field(default_factory=lambda: dict(p95_max=96.0,
                                                                 black_max=3.0))

    def label(self) -> str:
        return '%s（%s + %s）' % (self.name, self.stock, self.paper)

    def __str__(self):
        rms = self.texture.get('rms_granularity') or ()
        rms_s = ('%.1f' % (sum(rms) / len(rms))) if rms else '—'
        return ('%-10s %-22s rms=%-5s 画幅=%-4s 柔光=%-6s 彩度=%-5s 交付=%s'
                % (self.name, self.stock, rms_s,
                   self.style.get('film_format_mm', '—'),
                   self.style.get('diffusion', 'off'),
                   self.style.get('amount', '—'),
                   self.export.get('long_side', '—')))


def cards() -> Dict[str, Card]:
    """现有全部卡（扫引擎侧目录读它们真实绑定的东西）。"""
    out: Dict[str, Card] = {}
    for base in CARD_DIRS:
        for f in sorted(glob.glob(os.path.join(base, '*', 'preset.json'))):
            try:
                j = json.load(open(f, encoding='utf-8'))
            except Exception:
                continue
            sim = j.get('simulation') or {}
            grain = j.get('grain') or {}
            hal = j.get('halation') or {}
            coup = j.get('couplers') or {}
            name = os.path.basename(os.path.dirname(f))
            out[name] = Card(
                name=name,
                stock=sim.get('film_stock') or '?',
                paper=sim.get('print_paper') or '?',
                texture=dict(rms_granularity=tuple(grain.get('rms_granularity') or ()),
                             halation_strength=tuple(hal.get('halation_strength') or ()),
                             mult_usm=grain.get('mult_usm_amount'),
                             dye_um=grain.get('blur_dye_clouds_um')),
                style=dict(film_format_mm=sim.get('film_format_mm'),
                           amount=coup.get('amount'),
                           diffusion=_diffusion_of(sim)),
                export=dict((j.get('export') or {}) or (sim.get('export') or {})),
                impl=f)
    return out


def _diffusion_of(sim: dict) -> str:
    """柔光档：引擎侧用的是**扁平键**（`simulation.camera_diffusion_filter_*` / `..._diffusion_filter_*`）。

    相机端 = 拍摄时的柔光镜；印放端 = 放大时的柔光。两边各记一档。
    """
    parts = []
    for pre, tag in (('camera_diffusion_filter', '相机'), ('diffusion_filter', '印放')):
        if sim.get(pre + '_active'):
            fam = str(sim.get(pre + '_family') or '?').replace('black_pro_mist', 'BPM')
            parts.append('%s %s %.3g' % (tag, fam, sim.get(pre + '_strength') or 0))
    return ' / '.join(parts) if parts else 'off'


def card_for(stock: str, paper: str, table: Optional[Dict[str, Card]] = None
             ) -> Optional[Card]:
    table = table if table is not None else cards()
    for c in table.values():
        if c.stock == stock and c.paper == paper:
            return c
    return None


def card_axes(card: Card, reg: Optional[Registry] = None) -> Dict[str, object]:
    """这张卡用于匹配的属性 = 卷的性格 + 卡自己的质感档位。

    卷的性格来自 profile / 引擎卷表（有依据）；卡的档位是**这张卡的选择**。
    """
    reg = reg or _registry()
    st: Optional[Stock] = reg.stocks.get(card.stock)
    out: Dict[str, object] = dict(name=card.name, stock=card.stock, paper=card.paper)
    if st is not None:
        out.update(sepR=st.sepR, sepB=st.sepB, gamma=st.gamma, dmax=st.dmax,
                   anti=st.anti, rms_spec=st.rms)
    rms = card.texture.get('rms_granularity') or ()
    out['rms'] = (sum(rms) / len(rms)) if rms else (out.get('rms_spec') or ())
    if isinstance(out.get('rms'), tuple):
        out['rms'] = (sum(out['rms']) / len(out['rms'])) if out['rms'] else 0.0
    out['format_mm'] = card.style.get('film_format_mm')
    out['amount'] = card.style.get('amount')
    return out


if __name__ == '__main__':
    tb = cards()
    print('卡 %d 张' % len(tb))
    for c in tb.values():
        print('  ' + str(c))
