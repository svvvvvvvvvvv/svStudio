# -*- coding: utf-8 -*-
"""按**同输入实测**给每张卡选卷/纸（颜色优先）。

为什么这样选才对：
  · 在作者 test 图上比"哪支卷更饱和"是不可搬的 —— 那张图的脸和我们的被摄者不是一回事
    （实测：作者图上 XTRA 最饱和，但同一张 RAW 上 XTRA 反而比 EKTAR 低 4 个 C）；
  · 所以先做一张**同输入转移表**：拿一张真实 RAW，过 11 个配方的引擎，
    量皮肤 L/C/h（`out/expected_skin.json`）—— 这张表才是"这支卷在我们片子上会出什么颜色"；
  · 再用每张卡**自己大师片**的皮肤 C/h 当目标，选转移表里最接近的配方。

用法：
  python analysis/medium/stock_match.py            # 出报告 + assignments_color.json
  python analysis/medium/stock_match.py --apply    # 直接写卡（走 apply_assignments 的字段同步）
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

RECIPE_PAIR = {
    'P160': ('kodak_portra_160', 'kodak_portra_endura'),
    'P400': ('kodak_portra_400', 'kodak_portra_endura'),
    'GOLD': ('kodak_gold_200', 'kodak_portra_endura'),
    'P800': ('kodak_portra_800', 'kodak_portra_endura'),
    'PUSH1': ('kodak_portra_800_push1', 'kodak_portra_endura'),
    'PUSH2': ('kodak_portra_800_push2', 'kodak_portra_endura'),
    'GREEN': ('kodak_portra_400', 'fujifilm_crystal_archive_typeii'),
    'XTRA': ('fujifilm_xtra_400', 'fujifilm_crystal_archive_typeii'),
    'EKTAR': ('kodak_ektar_100', 'kodak_portra_endura'),
    'C200': ('fujifilm_c200', 'fujifilm_crystal_archive_typeii'),
    'P400H': ('fujifilm_pro_400h', 'fujifilm_crystal_archive_typeii'),
}

W_C = 1.0       # 色度权重
W_H = 0.6       # 色相权重（度 / 10）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--expected', default=str(common.OUT_DEFAULT / 'expected_skin.json'))
    ap.add_argument('--targets', default=str(common.OUT_DEFAULT / 'card_skin_target.json'))
    ap.add_argument('--out', default=str(common.OUT_DEFAULT))
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    exp = json.load(open(args.expected, encoding='utf-8'))
    table = {}
    for key, v in exp.items():
        stock, paper = [x.strip() for x in key.split('+')]
        code = next((k for k, p in RECIPE_PAIR.items() if p == (stock, paper)), None)
        if code and v.get('C') and (v.get('L') or 0) < 85 and 10 < (v.get('h') or 0) < 75:
            table[code] = dict(C=float(v['C']), h=float(v['h']), L=float(v['L'] or 0))
    targets = json.load(open(args.targets, encoding='utf-8'))

    rows = []
    for card, C, h in targets:
        scored = sorted((W_C * abs(table[c]['C'] - C) + W_H * min(abs(table[c]['h'] - h),
                                                                 360 - abs(table[c]['h'] - h)) / 10.0,
                         c) for c in table)
        best, second = scored[0], scored[1]
        rows.append(dict(card=card, target_C=C, target_h=h, recipe=best[1],
                         dist=round(best[0], 2), second=round(second[0], 2),
                         gap=round(second[0] - best[0], 2),
                         exp_C=round(table[best[1]]['C'], 1),
                         exp_h=round(table[best[1]]['h'], 1)))

    from collections import Counter
    print('== 同输入色度匹配（%d 卡）' % len(rows))
    for k, v in Counter(r['recipe'] for r in rows).most_common():
        print('  %-6s %2d 张 ｜ 该配方期望 C %.1f h %.1f'
              % (k, v, table[k]['C'], table[k]['h']))
    gaps = [r['gap'] for r in rows]
    print('  与次名差距：中位 %.2f ｜ >0.3 的 %d/%d'
          % (float(np.median(gaps)), sum(1 for g in gaps if g > 0.3), len(gaps)))

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, 'stock_match.json'), 'w', encoding='utf-8') as fh:
        json.dump(dict(table=table, rows=rows), fh, ensure_ascii=False, indent=1)

    def key(name):
        if name.startswith('xhs'):
            return 'xhs'
        if name.startswith('滨田'):
            return '滨田'
        return name[:3]

    assign = {key(r['card']): r['recipe'] for r in rows}
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assignments_color.json')
    with open(p, 'w', encoding='utf-8') as fh:
        json.dump(assign, fh, ensure_ascii=False, indent=1)
    print('写出', p, '（%d 条）' % len(assign))


if __name__ == '__main__':
    main()
