# -*- coding: utf-8 -*-
"""测量数据的验收（不是图像的验收）。设计真源 §62.4。

**为什么要有这个**：求解器的每一个数都从测量来。数据哪里不对，解出来的就是垃圾——
而且不会报错，只会"看起来能跑"。所以在拿数据去解之前，先让数据过一遍这几关。

用法：
    & $py -X utf8 design/measure_audit.py E:\\ds\\photo\\_corpus\\kurai\\full\\m_鹿井_v2.json ...

每一关都回答三个问题：**查什么 / 不过意味着什么 / 会不会挡住求解**。
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from typing import Dict, List, Optional, Sequence, Tuple

# 硬性门槛（不过 ⇒ 这批数据不能直接用来解）
MIN_SKIN_N = 300          # 颊区有效像素（口径下限；少于这个中位就不稳）
MIN_FACE_W = 60.0         # 原分辨率脸宽 px（再小，固定取样点会漂）
MIN_SET_N = 5             # 一套大师至少这么多张，才谈得上"分位带"
MAX_SET_P50_IQR = 15.0    # 套内 p50 的 IQR 超过它 ⇒ 这套本身混了两种场景
SKY_CONTAMINATED = 0.30   # 天空占比超过它 ⇒ p95 被天空顶住，统计 p95 要单独处理


def _rows(d) -> List[dict]:
    if isinstance(d, dict):
        return list(d.get('per_image') or [])
    return list(d or [])


def _set_of(row: dict) -> str:
    p = row.get('file') or row.get('path') or ''
    return os.path.basename(os.path.dirname(p)) or '?'


def _iqr(xs: Sequence[float]) -> float:
    xs = sorted(x for x in xs if x is not None)
    if len(xs) < 2:
        return 0.0
    n = len(xs)
    q1 = xs[int(0.25 * (n - 1))]
    q3 = xs[int(0.75 * (n - 1))]
    return float(q3 - q1)


def audit_one(path: str) -> dict:
    d = json.load(open(path, encoding='utf-8'))
    rows = _rows(d)
    meta = (d.get('_meta') if isinstance(d, dict) else {}) or {}
    out = dict(path=path, name=os.path.basename(path), n=len(rows),
               caliber=meta.get('caliber') or meta.get('sha') or '',
               issues=[], sets=[])

    # --- 关 1：字段齐全
    need = ('img', 'skin', 'bg', 'shape')
    miss = [k for k in need if not (rows and rows[0].get(k))]
    if miss:
        out['issues'].append(('字段缺失', '缺 %s ⇒ 求解器直接读不到，必须补' % ','.join(miss)))
    if not rows:
        out['issues'].append(('空数据', '一行都没有'))
        return out

    # --- 关 2：口径一致（同一份文件内）
    cal = {json.dumps(r.get('caliber'), sort_keys=True, ensure_ascii=False)
           for r in rows if r.get('caliber') is not None}
    if len(cal) > 1:
        out['issues'].append(('口径不一致', '同一文件里出现 %d 种 caliber ⇒ 混版了，不许直接比' % len(cal)))

    # --- 关 3：逐图可信度
    has_skin = [r for r in rows if (r.get('skin') or {}).get('L') is not None]
    small_n = [r for r in has_skin if ((r['skin'].get('n') or 0) < MIN_SKIN_N)]
    small_face = [r for r in rows
                  if (r.get('face_w_native') or 0) and r['face_w_native'] < MIN_FACE_W]
    out['n_with_skin'] = len(has_skin)
    out['pct_small_skin'] = 100.0 * len(small_n) / max(len(rows), 1)
    if small_n:
        out['issues'].append(('取样像素不足',
                              '%d 张 skin.n < %d（占 %.0f%%）⇒ 这些图的肤色只能当线索'
                              % (len(small_n), MIN_SKIN_N, out['pct_small_skin'])))
    if small_face:
        out['issues'].append(('脸太小', '%d 张 face_w < %.0f px ⇒ 固定取样点会漂' % (len(small_face), MIN_FACE_W)))

    # --- 关 4：内容污染（p95 被天空顶住）
    sky = [r.get('sky_share_frac') for r in rows if r.get('sky_share_frac') is not None]
    n_sky = len([s for s in sky if s >= SKY_CONTAMINATED])
    out['pct_sky'] = 100.0 * n_sky / max(len(sky), 1)
    if n_sky:
        out['issues'].append(('p95 被天空污染',
                              '%d 张天空占比 ≥ %.0f%% ⇒ 统计 p95 时要标出或剔除'
                              % (n_sky, 100 * SKY_CONTAMINATED)))

    # --- 关 5：逐套（这才是卡的来源）
    by: Dict[str, List[dict]] = {}
    for r in rows:
        by.setdefault(_set_of(r), []).append(r)
    for name, rs in sorted(by.items()):
        p50 = [(r.get('img') or {}).get('p50') for r in rs if (r.get('img') or {}).get('p50') is not None]
        p95 = [(r.get('img') or {}).get('p95') for r in rs if (r.get('img') or {}).get('p95') is not None]
        face = [(r.get('skin') or {}).get('L') for r in rs if (r.get('skin') or {}).get('L') is not None]
        iqr50 = _iqr(p50)
        bad = []
        if len(rs) < MIN_SET_N:
            bad.append('n=%d < %d ⇒ 分位带不可靠' % (len(rs), MIN_SET_N))
        if iqr50 > MAX_SET_P50_IQR:
            bad.append('套内 p50 的 IQR %.0f > %.0f ⇒ 这套混了两种场景' % (iqr50, MAX_SET_P50_IQR))
        out['sets'].append(dict(name=name, n=len(rs), n_skin=len(face),
                                p50=st.median(p50) if p50 else None,
                                p95=st.median(p95) if p95 else None,
                                p50_iqr=iqr50,
                                faceL=st.median(face) if face else None,
                                bad=bad))
    n_bad = len([s for s in out['sets'] if s['bad']])
    if n_bad:
        out['issues'].append(('套不合格', '%d/%d 套不能直接当卡用（见下表）' % (n_bad, len(out['sets']))))
    return out


def main(argv: List[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    report = [audit_one(p) for p in argv]
    for r in report:
        print('=' * 78)
        print('%s   行数 %d ｜ 有肤色的 %d' % (r['name'], r['n'], r.get('n_with_skin', 0)))
        if r['caliber']:
            print('口径 %s' % r['caliber'])
        if r['issues']:
            print('— 问题 —')
            for k, why in r['issues']:
                print('  ✗ %-12s %s' % (k, why))
        else:
            print('  ✓ 没有硬性问题')
        bad = [s for s in r['sets'] if s['bad']]
        if bad:
            print('— 不合格的套（前 10） —')
            for s in bad[:10]:
                print('  %-24s n=%-3d ｜ %s' % (s['name'], s['n'], '；'.join(s['bad'])))
        print('— 套的分布（前 8，p50 中位 / 套内 IQR / p95 中位 / 脸 L 中位） —')
        for s in sorted(r['sets'], key=lambda x: -(x['n'] or 0))[:8]:
            print('  %-24s n=%-3d p50=%5s IQR=%4.1f p95=%5s 脸L=%5s'
                  % (s['name'], s['n'],
                     '—' if s['p50'] is None else '%.1f' % s['p50'],
                     s['p50_iqr'],
                     '—' if s['p95'] is None else '%.1f' % s['p95'],
                     '—' if s['faceL'] is None else '%.1f' % s['faceL']))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
