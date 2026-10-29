# -*- coding: utf-8 -*-
"""测量桥：把 `measure_all.py` 的记录接进设计层。

三条规矩（分工设计 §10.1）：
  1. 三态必须**同一套测量代码** ⇒ 用 `measure_sha()` 做口径指纹；
  2. 换口径 ⇒ 三态一起重测，不许只重测一态；
  3. 每个旋钮只能对着它作用之前那一态量 ⇒ 只按 stem 过滤，不混态。

用法：
    from design.measure import load, measure_sha, only
    recs = load(r'E:\\ds\\photo\\_corpus\\kurai\\full\\m_state1.json')
    eng = only(recs, '_engine.tif')
    print(measure_sha())
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Dict, List

# 测量层的两个真源文件
SRC = [
    r'E:\ds\photo\_corpus\kurai\full\measure_all.py',
    r'E:\ds\photo\_corpus\kurai\full\skin_point.py',
    # 必须带上：measure_all 的检测/对齐/缩放全在这里（换它 = 换尺子）
    r'E:\ds\photo\_corpus\kurai\skin_v2\skin_v2.py',
]


def measure_sha() -> str:
    """口径指纹：**所有**测量依赖文件一起 hash。三态共用同一个值。

    2026-10-29 修：原先只 hash 两个文件，漏了 skin_v2（检测/对齐都在里面）
    => 换测量器不会改 sha => "三态同一把尺子"变成空话。
    """
    h = hashlib.sha256()
    for p in SRC:
        try:
            with open(p, 'rb') as fh:
                h.update(fh.read())
        except OSError:
            h.update(b'(missing %s)' % p.encode())
    return h.hexdigest()[:12]


def load(path: str) -> Dict[str, dict]:
    """读一份 measure_all 输出，返回 {文件名: 记录}。"""
    d = json.load(open(path, encoding='utf-8'))
    rows = d.get('per_image') or []
    if isinstance(rows, dict):                      # 兼容旧格式
        return dict(rows)
    return {r['file']: r for r in rows if r.get('skin')}


def load_all(path: str) -> Dict[str, dict]:
    """读一份 measure_all 输出，**不过滤没皮肤的图**。

    为什么要这个（2026-10-29 真跑）：`load()` 只留有 skin 的记录 ⇒ 没脸的图
    （DSCF1726）在验收页里直接消失，而它恰恰是要被检查的那一张。
    画像/匹配仍然用 `load()`（那里确实只要脸）。
    """
    d = json.load(open(path, encoding='utf-8'))
    rows = d.get('per_image') or []
    if isinstance(rows, dict):
        return dict(rows)
    return {r['file']: r for r in rows}


def meta(path: str) -> dict:
    d = json.load(open(path, encoding='utf-8'))
    return d.get('_meta') or {}


def only(recs: Dict[str, dict], pattern: str) -> Dict[str, dict]:
    """按文件名后缀/正则筛，防止把「_sim.png」这类仿真预览混进引擎片。"""
    rx = re.compile(pattern)
    return {k: v for k, v in recs.items() if rx.search(k)}


def stems(recs: Dict[str, dict]) -> List[str]:
    return sorted(recs)


def summarize(recs: Dict[str, dict]) -> dict:
    """一批记录的中位摘要（只用于日志，不进判据）。"""
    import statistics as st

    def med(key, sub=None):
        xs = []
        for r in recs.values():
            v = (r.get(sub) or {}).get(key) if sub else r.get(key)
            if isinstance(v, (int, float)):
                xs.append(float(v))
        return round(st.median(xs), 2) if xs else None

    return dict(n=len(recs), p50=med('p50', 'img'), p99=med('p99', 'img'),
                clip=med('clip_pct', 'img'), faceL=med('L', 'skin'),
                faceC=med('C', 'skin'), faceh=med('h', 'skin'))
