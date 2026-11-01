# -*- coding: utf-8 -*-
"""卡 → 引擎能吃的**介质块**（`引擎设计.md` §3）。

没有"预设"这个可选集合：每个目录（卡）自带一份介质
（`analysis/corpus/kurai/media/<卡名>.json`），引擎直接吃这个 dict。
读不到就抛 —— 目录没介质是"未定义"，不是"用默认"。
"""
from __future__ import annotations


def medium_of(card) -> dict:
    """这张卡的介质块（卷/纸/WB/amount/化学…）。没有就抛。"""
    from .medium import medium_of as _medium_of
    return _medium_of(card)
