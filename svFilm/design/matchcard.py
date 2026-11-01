# -*- coding: utf-8 -*-
"""卡注册表：**卡 = 大师成片库里的一个目录**（`设计真源.md` §4）。

这里只做三件事：
  · `cards()`      —— 列出可用的卡（薄卡不列）；
  · `raw_card()`   —— 按名字取原样卡字典（plan 的目标 + 卡级 WB + PE）；
  · `card_samples()` —— 卡的样片绝对路径（人选 hero 用）。

卡不是我们建的，是从库里选的；介质（卷/纸/WB/PE）按目录存在
`analysis/corpus/kurai/media/<卡名>.json` 与 `card_pe.json`，不在这里硬编码。
匹配/排序不在这里（匹配 A 用 `contentmatch` + `card_content`）。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_CORPUS = r'E:\svStudio\svFilm\analysis\corpus\kurai'
# v6 = 从 photos_tone_v04（1080 交付域）重建；读不到再退回 v5（旧 v7 尺子）
_CARDS = os.path.join(_CORPUS, 'cards_v6.json')
_CARDS_FALLBACK = os.path.join(_CORPUS, 'cards_v5.json')
_MEDIA = os.path.join(_CORPUS, 'media')
_PE = os.path.join(_CORPUS, 'card_pe.json')
_TONE = os.path.join(_CORPUS, 'card_tone.json')
# 大师成片根：cards 里的 samples 是 <卡目录>/<文件> 的相对路径
_MASTER = r'E:\ds\大师成片'

_CACHE: Dict[str, dict] = {}


@dataclass
class MatchCard:
    """一套风格（卡的瘦身视图，给渲染/列表用）。"""

    name: str
    n: int = 0
    stock: str = ''
    paper: str = ''
    corpus: str = ''
    samples: List[str] = field(default_factory=list)
    profile: Dict[str, float] = field(default_factory=dict)
    wb: Dict[str, object] = field(default_factory=dict)
    thin: bool = False

    def label(self) -> str:
        return '%s（%s + %s）' % (self.name, self.stock, self.paper)


def _load(name: str):
    if name in ('cards', 'pe', 'tone'):
        if name in _CACHE:
            return _CACHE[name]
        path = {'cards': (_CARDS if os.path.isfile(_CARDS) else _CARDS_FALLBACK),
                'pe': _PE, 'tone': _TONE}[name]
        try:
            d = json.load(open(path, encoding='utf-8'))
        except Exception:                                  # noqa: BLE001
            d = {}
        _CACHE[name] = d
        return d
    return None


def _medium(name: str) -> dict:
    p = os.path.join(_MEDIA, name + '.json')
    if not os.path.isfile(p):
        return {}
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:                                      # noqa: BLE001
        return {}


def _abs_samples(card: dict) -> List[str]:
    rel = list(card.get('samples') or [])
    if not rel or any(os.path.isabs(p) for p in rel):
        return rel
    root = os.path.join(_MASTER, card.get('corpus') or '')
    return [os.path.join(root, p) for p in rel]


def raw_cards() -> List[dict]:
    """原样卡字典（bands / points / neutral_axis / samples 全在）。"""
    d = _load('cards')
    return list(d.get('cards') or [])


def cards() -> List[MatchCard]:
    """全部可用卡（薄卡不进匹配，只留档）。介质从 media/<卡名>.json 读。"""
    out: List[MatchCard] = []
    for c in raw_cards():
        m = _medium(c['name'])
        sim = (m.get('simulation') or {})
        prof = {}
        for k in ('p50', 'p95', 'p5', 'contrast'):
            v = (c.get('points') or {}).get(k)
            if isinstance(v, (int, float)):
                prof[k] = float(v)
        out.append(MatchCard(
            name=c['name'], n=int(c.get('n') or 0),
            stock=str(sim.get('film_stock') or ''),
            paper=str(sim.get('print_paper') or ''),
            corpus=str(c.get('corpus') or ''),
            samples=_abs_samples(c), profile=prof,
            wb=dict(c.get('wb') or {}),
            thin=bool(c.get('thin'))))
    return [c for c in out if not c.thin]


def raw_card(name: str) -> Optional[dict]:
    """按名字取原样卡字典（plan 的入参）。

    带上：`tone`（暗部/高光冷暖，card_tone.json）与 `pe`（引擎印相曝光，card_pe.json）。
    """
    for c in raw_cards():
        if c.get('name') == name:
            extra = {}
            t = (_load('tone') or {}).get(name)
            if t:
                extra['tone'] = t
            pe = (_load('pe') or {}).get(name) or {}
            if pe.get('pe') is not None:
                extra['pe'] = float(pe['pe'])
            return dict(c, **extra) if extra else c
    return None


def card_samples(name: str) -> List[str]:
    """某张卡的样片（绝对路径）。人从这里点一张当 hero。"""
    c = raw_card(name)
    return _abs_samples(c) if c else []
