# -*- coding: utf-8 -*-
"""条件带：把"光位"当条件，看它到底能解释什么。

只读测量，不渲染。回答三个问题：

  1 卡内方差里，有多少是"光位"解释的（eta²）？
  2 条件化之后，带是变窄了、还是只挪了中心？
  3 两套光位标签（卡用的 v1 / 设计里的双区 v2）差多少？

跑：
  & $py -X utf8 -m design.cond_bands --corpus 鹿井
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Dict, List, Optional, Tuple

import numpy as np

ROOT = r'E:\ds\photo\_corpus\kurai\full'

# 参与分析的量：名字 → 取值函数（都是"卡上有的"）
Q = {
    'face_L':      lambda r: (r.get('skin') or {}).get('L'),
    'face_over_bg': lambda r: ((r.get('skin') or {}).get('L') /
                               (r.get('bg') or {}).get('L_p90')
                               if (r.get('skin') or {}).get('L') and
                               ((r.get('bg') or {}).get('L_p90') or 0) >= 20 else None),
    'face_C':      lambda r: (r.get('skin') or {}).get('C'),
    'face_h':      lambda r: (r.get('skin') or {}).get('h'),
    'contrast':    lambda r: ((r.get('img') or {}).get('p95') or 0) - ((r.get('img') or {}).get('p5') or 0)
                             if (r.get('img') or {}).get('p95') is not None else None,
    'p50':         lambda r: (r.get('img') or {}).get('p50'),
    'toe':         lambda r: ((r.get('shape') or {}).get('toe')),
    'shoulder':    lambda r: ((r.get('shape') or {}).get('shoulder')),
    'contrast_win': lambda r: ((r.get('shape') or {}).get('contrast_win')),
    'warm_cool_b': lambda r: r.get('warm_cool_b'),
}


def label_v1(r: dict) -> str:
    """卡在用的那套（cards_build.light_label）：近场比 + 明暗聚拢 + 左右差。"""
    li = r.get('light') or {}
    cl, rn, dl = li.get('clump'), li.get('ratio_near90'), li.get('d_lr_img')
    if cl is not None and cl > 0.53:
        return 'dappled'
    if rn is not None and rn < 0.78:
        return 'back'
    if rn is not None and rn < 0.95:
        return 'back_side'
    if dl is not None and abs(dl) > 8.0:
        return 'side'
    if rn is None:
        return 'unknown'
    return 'front'


def label_v2(r: dict) -> str:
    """设计文档的双区判据（缺"脸内暗占比"字段，这里只用 gap 那一半）。

    gap = 背景亮部 − 脸；≥35 ⇒ 逆光，≤12 ⇒ 顺光，中间留 mid（不硬判）。
    """
    sk = (r.get('skin') or {}).get('L')
    bg = (r.get('bg') or {}).get('L_p90')
    if sk is None or not bg:
        return 'unknown'
    gap = float(bg) - float(sk)
    if gap >= 35:
        return 'back'
    if gap <= 12:
        return 'front'
    return 'mid'


def _band(xs: List[float], lo: int = 25, hi: int = 75) -> Optional[List[float]]:
    xs = [float(x) for x in xs if x is not None and np.isfinite(x)]
    if len(xs) < 5:
        return None
    return [round(float(np.percentile(xs, lo)), 2), round(float(np.percentile(xs, hi)), 2)]


def _eta2(rows: List[dict], key: str, lab) -> Optional[float]:
    """卡内 eta²：光位能解释卡内多少方差（池化）。"""
    ss_b = ss_t = 0.0
    by_card: Dict[str, List[Tuple[str, float]]] = {}
    for r in rows:
        v = Q[key](r)
        if v is None or not np.isfinite(v):
            continue
        card = os.path.basename(os.path.dirname(r.get('file') or ''))
        by_card.setdefault(card, []).append((lab(r), float(v)))
    for card, items in by_card.items():
        if len(items) < 6:
            continue
        vals = np.array([v for _, v in items])
        gm = vals.mean()
        ss_t += float(((vals - gm) ** 2).sum())
        groups: Dict[str, List[float]] = {}
        for k, v in items:
            groups.setdefault(k, []).append(v)
        for g, vs in groups.items():
            if len(vs) < 2:
                continue
            m = float(np.mean(vs))
            ss_b += len(vs) * (m - gm) ** 2
    if ss_t <= 0:
        return None
    return round(ss_b / ss_t, 3)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--corpus', default='鹿井')
    ap.add_argument('--out', default=r'E:\ds\photo\_run_1030_card004\cond_bands.json')
    a = ap.parse_args(argv)

    p = os.path.join(ROOT, 'm_%s_v3.json' % a.corpus)
    rows = json.load(open(p, encoding='utf-8')).get('per_image') or []
    print('%s：%d 张（有 skin %d）' % (a.corpus, len(rows), sum(1 for r in rows if r.get('skin'))))

    out = dict(corpus=a.corpus, n=len(rows), labelers={}, quantities={})

    for name, lab in (('v1_cards', label_v1), ('v2_gap', label_v2)):
        print('\n== 标签 %s ==' % name)
        classes: Dict[str, List[dict]] = {}
        for r in rows:
            classes.setdefault(lab(r), []).append(r)
        print('  类别：' + '，'.join('%s=%d' % (k, len(v)) for k, v in sorted(classes.items())))

        # 每张卡的类别集中度（是不是"一套 = 一种光位"）
        dom = []
        by_card: Dict[str, List[str]] = {}
        for r in rows:
            card = os.path.basename(os.path.dirname(r.get('file') or ''))
            by_card.setdefault(card, []).append(lab(r))
        for card, ls in by_card.items():
            if len(ls) < 5:
                continue
            vals, cnts = np.unique(ls, return_counts=True)
            dom.append(float(cnts.max()) / len(ls))
        print('  卡内主类占比：中位 %.2f（1.00 = 每张卡只有一种光位）' % float(np.median(dom)))

        rec = dict(classes={k: len(v) for k, v in classes.items()},
                   dom_share_median=round(float(np.median(dom)), 3),
                   bands={}, eta2={})

        for key in Q:
            g = _band([Q[key](r) for r in rows], 25, 75)
            cb = {}
            for k, rs in sorted(classes.items()):
                b = _band([Q[key](r) for r in rs], 25, 75)
                if b:
                    cb[k] = b
            rec['bands'][key] = dict(global_=g, conditional=cb)
            e = _eta2(rows, key, lab)
            rec['eta2'][key] = e
        out['labelers'][name] = rec

        # 打印：全局带 vs 条件带 + eta²
        print('  %-12s %-16s %-28s %6s' % ('量', '全局 P25-P75', '条件带(每类)', 'eta²'))
        for key in Q:
            b = rec['bands'][key]
            cond = ' | '.join('%s %s' % (k, v) for k, v in (b['conditional'] or {}).items())
            print('  %-12s %-16s %-28s %6s'
                  % (key, str(b['global_']), cond[:28], rec['eta2'][key]))

    json.dump(out, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n写出 %s' % a.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
