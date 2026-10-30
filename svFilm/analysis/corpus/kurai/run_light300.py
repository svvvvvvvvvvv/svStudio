# -*- coding: utf-8 -*-
"""挑 300 张大师成片，跑光向量探针。"""
from __future__ import annotations

import json
import os
import sys
import time

BASE = r'E:\ds\photo\_corpus\kurai\full'
for p in (BASE, r'E:\ds\photo\_corpus\kurai\skin_v2'):
    if p not in sys.path:
        sys.path.insert(0, p)

import light_probe as LP

N_TARGET = 300
OUT = os.path.join(BASE, 'light_300.json')


def sample():
    rows = []
    with open(os.path.join(BASE, 'images.jsonl'), encoding='utf-8') as f:
        for line in f:
            d = json.loads(line)
            sv = d.get('skin_v2') or {}
            if not sv.get('ok'):
                continue
            p = d.get('path') or ''
            if not os.path.isfile(p):
                continue
            rows.append((d.get('corpus'), p))
    by = {}
    for c, p in rows:
        by.setdefault(c, []).append(p)
    want = {'鹿井': 240, 'xhs': 40, '滨田': 20}
    out = []
    for c, lst in by.items():
        k = want.get(c, 0)
        if not k:
            continue
        step = max(1, len(lst) // k)
        out += lst[::step][:k]
    return out


def main():
    files = sample()
    print('样本 %d 张（鹿井/xhs/滨田 分层）' % len(files))
    t0 = time.time()
    from multiprocessing import Pool
    rows = []
    with Pool(4) as pool:
        for i, r in enumerate(pool.imap_unordered(LP._worker, files, chunksize=2), 1):
            rows.append(r)
            if i % 50 == 0:
                print('  %d/%d  %.0fs' % (i, len(files), time.time() - t0))
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(dict(ver=LP.VERSION, n=len(rows), rows=rows), fh,
                  ensure_ascii=False, indent=1)
    import collections
    k = collections.Counter(r.get('kind') for r in rows)
    print('->', OUT, '共 %.0f 秒' % (time.time() - t0))
    print('判型分布：', dict(k.most_common()))


if __name__ == '__main__':
    main()
