# -*- coding: utf-8 -*-
"""条件带 v2：统一光位判据 + 二维分桶 + 判"卡到底分不分得开"。

只读测量，不渲染。回答四个问题：

  1 光位（双区判据）能解释多少方差？天空呢？两者合起来呢？
  2 条件化之后，带窄了多少？
  3 **卡与卡之间**的差异占多少（eta²(card)）——这是"卡还能不能用来匹配"的判据
  4 若条件分桶分到能解释的粒度，每桶还有多少样本（够不够给每张卡单独的条件带）

跑：
  & $py -X utf8 -m design.cond_bands --measure <m_xxx.json>
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Dict, List, Optional

import numpy as np

Q = {
    'face_L':       lambda r: (r.get('skin') or {}).get('L'),
    'face_over_bg': lambda r: ((r.get('skin') or {}).get('L') /
                               (r.get('bg') or {}).get('L_p90')
                               if (r.get('skin') or {}).get('L') and
                               ((r.get('bg') or {}).get('L_p90') or 0) >= 20 else None),
    'face_C':       lambda r: (r.get('skin') or {}).get('C'),
    'face_h':       lambda r: (r.get('skin') or {}).get('h'),
    'contrast':     lambda r: (((r.get('img') or {}).get('p95') or 0) - ((r.get('img') or {}).get('p5') or 0)
                               if (r.get('img') or {}).get('p95') is not None else None),
    'p50':          lambda r: (r.get('img') or {}).get('p50'),
    'toe':          lambda r: ((r.get('shape') or {}).get('toe')),
    'shoulder':     lambda r: ((r.get('shape') or {}).get('shoulder')),
    'contrast_win': lambda r: ((r.get('shape') or {}).get('contrast_win')),
    'warm_cool_b':  lambda r: r.get('warm_cool_b'),
    'face_p90':     lambda r: r.get('face_L_p90'),
}

SKY_T = 0.20          # 天空占比 ≥20% 算"有天空"（只作分桶，不作判据）


def label_dual(r: dict) -> str:
    """双区判据（设计 §14.1.1）：gap + 脸内暗占比。会漏，不判反。"""
    sk = (r.get('skin') or {}).get('L')
    bg = (r.get('bg') or {}).get('L_p90')
    dk = r.get('face_dark_share40')
    if sk is None or not bg or dk is None:
        return 'unknown'
    gap = float(bg) - float(sk)
    if gap >= 35.0 or (dk >= 0.35 and gap >= 12.0):
        return 'back'
    if dk <= 0.12 and gap <= 12.0:
        return 'front'
    return 'mid'


def is_sky(r: dict) -> str:
    v = r.get('sky_share_frac')
    if v is None:
        return 'unknown'
    return 'sky' if float(v) >= SKY_T else 'nosky'


def _band(xs: List[float], lo: int = 25, hi: int = 75) -> Optional[List[float]]:
    xs = [float(x) for x in xs if x is not None and np.isfinite(x)]
    if len(xs) < 8:
        return None
    return [round(float(np.percentile(xs, lo)), 2), round(float(np.percentile(xs, hi)), 2)]


def eta2(rows: List[dict], key: str, factor) -> Optional[float]:
    """单因子方差解释率（总方差里，因子组间占多少）。"""
    vals, groups = [], []
    for r in rows:
        v = Q[key](r)
        if v is None or not np.isfinite(v):
            continue
        vals.append(float(v))
        groups.append(factor(r))
    if len(vals) < 30:
        return None
    vals = np.asarray(vals)
    gm = vals.mean()
    ss_t = float(((vals - gm) ** 2).sum())
    if ss_t <= 0:
        return None
    ss_b = 0.0
    for g in set(groups):
        m = np.array([v for v, gg in zip(vals, groups) if gg == g])
        if len(m) >= 2:
            ss_b += len(m) * (m.mean() - gm) ** 2
    return round(ss_b / ss_t, 3)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--measure', default=r'E:\svStudio\svFilm\analysis\corpus\kurai\m_鹿井_v4.json')
    ap.add_argument('--out', default=r'E:\svStudio\svFilm\analysis\data\cond_bands_v2.json')
    ap.add_argument('--table-out', default='', help='另写一份"给求解器吃"的条件表')
    ap.add_argument('--corpus', default='鹿井')
    a = ap.parse_args(argv)

    d = json.load(open(a.measure, encoding='utf-8'))
    rows = [r for r in (d.get('per_image') or []) if r.get('skin')]
    print('口径文件 %s' % os.path.basename(a.measure))
    print('有脸 %d 张 ｜ 卡数 %d' % (len(rows), len({os.path.dirname(r['file']) for r in rows})))

    card = lambda r: os.path.dirname(r.get('file') or '')
    light = label_dual
    cell = lambda r: '%s/%s' % (label_dual(r), is_sky(r))

    # ---- 分桶样本
    from collections import Counter
    print('\n光位：', dict(Counter(light(r) for r in rows)))
    print('天空：', dict(Counter(is_sky(r) for r in rows)))
    print('光位×天空：')
    for k, v in sorted(Counter(cell(r) for r in rows).items()):
        print('  %-16s %4d' % (k, v))

    # ---- 解释率
    print('\n%-13s %8s %8s %8s %8s %6s' % ('量', 'eta²卡', 'eta²光位', 'eta²天空', 'eta²格子', '每格中位n'))
    out = dict(measure=os.path.basename(a.measure), n=len(rows), factors={}, bands={})
    cards_n = Counter(card(r) for r in rows)
    for key in Q:
        e_card = eta2(rows, key, card)
        e_light = eta2(rows, key, light)
        e_sky = eta2(rows, key, is_sky)
        e_cell = eta2(rows, key, cell)
        # 每格中位样本数（只算有值的）
        per = Counter(cell(r) for r in rows if Q[key](r) is not None)
        med_n = int(np.median(list(per.values()))) if per else 0
        print('%-13s %8s %8s %8s %8s %6d' % (key, e_card, e_light, e_sky, e_cell, med_n))
        out['factors'][key] = dict(card=e_card, light=e_light, sky=e_sky, cell=e_cell)

        g = _band([Q[key](r) for r in rows], 25, 75)
        b = {}
        for k in sorted(set(cell(r) for r in rows)):
            bb = _band([Q[key](r) for r in rows if cell(r) == k], 25, 75)
            if bb:
                b[k] = bb
        out['bands'][key] = dict(global_=g, cell=b)

    # ---- 带宽收窄
    print('\n带宽（全局 → 条件格子）：')
    for key in ('face_L', 'face_over_bg', 'contrast', 'warm_cool_b'):
        g = out['bands'][key]['global_']
        if not g:
            continue
        w = g[1] - g[0]
        row = ['%s %.2f→%.0f%%' % (k, w, 100 * ((v[1] - v[0]) / w)) for k, v in
               sorted(out['bands'][key]['cell'].items()) if v]
        print('  %-13s 全局宽 %.2f ｜ %s' % (key, w, '  '.join(row)))

    json.dump(out, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n写出 %s' % a.out)

    # ---- 给求解器吃的条件表：只留"够样本"的格子，带 + n
    if a.table_out:
        tbl = dict(_meta=dict(corpus=a.corpus, labeler='dual-v1', measure=os.path.basename(a.measure),
                              n=len(rows), note='P25-P75；n<30 的格子不给带'),
                   classes={})
        for k in ('back', 'mid', 'front'):
            rs = [r for r in rows if label_dual(r) == k]
            if len(rs) < 30:
                tbl['classes'][k] = dict(n=len(rs), usable=False)
                continue
            tbl['classes'][k] = dict(
                n=len(rs), usable=True,
                face_over_bg=_band([Q['face_over_bg'](r) for r in rs]),
                face_L=_band([Q['face_L'](r) for r in rs]),
            )
        json.dump(tbl, open(a.table_out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('写出条件表 %s' % a.table_out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
