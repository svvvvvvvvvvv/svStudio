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
]


def measure_sha() -> str:
    """口径指纹：两个测量文件内容一起 hash。三态共用同一个值。"""
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
