# -*- coding: utf-8 -*-
"""目录内容卡的标注一致性（Cohen's κ）。

用法：
    & $py -X utf8 analysis/corpus/kurai/kappa_content.py `
        --a card_content_manual.json --b card_content_gold5.json

字段：scene / clothing.type / clothing.palette / palette / light（分类）；
      elements（多选，报 Jaccard）。
κ<0.4 不用；0.4–0.6 只做硬过滤；≥0.6 才进分数。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from typing import Dict, List


def _load(path: str) -> Dict[str, dict]:
    d = json.load(open(path, encoding='utf-8'))
    return {c['card']: c for c in (d.get('cards') or []) if c.get('card')}


def _kappa(a: List[str], b: List[str]):
    n = len(a)
    if n == 0:
        return None, 0
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca[k] / n) * (cb[k] / n) for k in set(ca) | set(cb))
    if pe >= 1.0 - 1e-9:
        return 1.0, n
    return (po - pe) / (1.0 - pe), n


def _cat(c: dict, field: str):
    if field == 'clothing.type':
        return (c.get('clothing') or {}).get('type')
    if field == 'clothing.palette':
        return (c.get('clothing') or {}).get('palette')
    return c.get(field)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--a', required=True)
    ap.add_argument('--b', required=True)
    a = ap.parse_args(argv)
    A, B = _load(a.a), _load(a.b)
    common = sorted(set(A) & set(B))
    print('共同卡 %d / A %d / B %d' % (len(common), len(A), len(B)))
    if not common:
        return 1
    for field in ('scene', 'clothing.type', 'clothing.palette', 'palette', 'light'):
        va, vb = [], []
        for k in common:
            x, y = _cat(A[k], field), _cat(B[k], field)
            if x in (None, 'unknown') or y in (None, 'unknown'):
                continue
            va.append(str(x))
            vb.append(str(y))
        k, n = _kappa(va, vb)
        if k is None:
            print('%-18s  n=0  （两边都缺/unknown）' % field)
        else:
            print('%-18s  n=%-3d  κ=%+.2f  %s' % (
                field, n, k,
                '可用' if k >= 0.6 else ('只做硬过滤' if k >= 0.4 else '不用')))
    # elements：Jaccard
    js = []
    for k in common:
        ea = set(A[k].get('elements') or [])
        eb = set(B[k].get('elements') or [])
        if ea or eb:
            js.append(len(ea & eb) / max(1, len(ea | eb)))
    if js:
        print('%-18s  n=%-3d  Jaccard=%.2f' % ('elements', len(js), sum(js) / len(js)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
