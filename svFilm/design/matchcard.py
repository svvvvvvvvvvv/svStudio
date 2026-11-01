# -*- coding: utf-8 -*-
"""卡注册表：**卡 = 大师成片库里的一个目录**（`设计真源.md` §4）。

一个目录一份 JSON：`analysis/corpus/kurai/cards/<卡名>.json`，
里面同时带点值/部位/介质/PE/tone/内容；不再拆 `cards_v7` + `card_pe` +
`card_tone` + `card_medium` + `media/`。

这里只做三件事：
  · `cards()`      —— 列出可用的卡（薄卡不列）；
  · `raw_card()`   —— 按名字取完整卡字典（plan 的目标 + 介质 + WB + PE + tone）；
  · `card_samples()` —— 卡的样片绝对路径（人选 hero 用）。
"""
from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_CORPUS = r'E:\svStudio\svFilm\analysis\corpus\kurai'
_CARDS_DIR = os.path.join(_CORPUS, 'cards')
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


def _load_path(path: str) -> dict:
    if path in _CACHE:
        return _CACHE[path]
    try:
        d = json.load(open(path, encoding='utf-8'))
    except Exception:                                      # noqa: BLE001
        d = {}
    _CACHE[path] = d
    return d


def _card_path(name: str) -> str:
    return os.path.join(_CARDS_DIR, name + '.json')


def _abs_samples(card: dict) -> List[str]:
    rel = list(card.get('samples') or [])
    if not rel or any(os.path.isabs(p) for p in rel):
        return rel
    root = os.path.join(_MASTER, card.get('corpus') or '')
    return [os.path.join(root, p) for p in rel]


def raw_cards() -> List[dict]:
    """原样卡字典（一个目录一份 JSON）。"""
    out: List[dict] = []
    for p in sorted(glob.glob(os.path.join(_CARDS_DIR, '*.json'))):
        d = _load_path(p)
        if isinstance(d, dict) and d.get('name'):
            out.append(d)
    return out


def cards() -> List[MatchCard]:
    """全部可用卡（薄卡不进匹配，只留档）。"""
    out: List[MatchCard] = []
    for c in raw_cards():
        m = c.get('medium') or {}
        sim = m.get('simulation') or {}
        prof = {}
        for k in ('p50', 'p95', 'p5', 'contrast'):
            v = (c.get('points') or {}).get(k)
            if isinstance(v, (int, float)):
                prof[k] = float(v)
        out.append(MatchCard(
            name=c['name'], n=int(c.get('n') or 0),
            stock=str(sim.get('film_stock') or c.get('stock') or ''),
            paper=str(sim.get('print_paper') or c.get('paper') or ''),
            corpus=str(c.get('corpus') or ''),
            samples=_abs_samples(c), profile=prof,
            wb=dict(c.get('wb') or {}),
            thin=bool(c.get('thin'))))
    return [c for c in out if not c.thin]


def raw_card(name: str) -> Optional[dict]:
    """按名字取完整卡字典（点值/部位/medium/pe/tone/content 都在里面）。"""
    p = _card_path(name)
    if not os.path.isfile(p):
        return None
    c = _load_path(p)
    return dict(c) if isinstance(c, dict) else None


def card_samples(name: str) -> List[str]:
    """某张卡的样片（绝对路径）。人从这里点一张当 hero。"""
    c = raw_card(name)
    return _abs_samples(c) if c else []
