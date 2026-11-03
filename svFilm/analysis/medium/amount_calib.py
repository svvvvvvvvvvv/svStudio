# -*- coding: utf-8 -*-
"""按大师作品的**肤色色度**给每张卡定 `couplers.amount`（颜色校准的主旋钮）。

依据（实测，DSCF4332 / 卡 129 / PE 0.30）：
    amount 0.4 → 皮肤 C 17.2
    amount 0.6 → 19.4
    amount 0.8 → 22.8
    amount 1.0 → 27.8        （C 随 amount 近似线性：C ≈ 10.1 + 17.8·amount）
大师片 729 张：皮肤 C 中位 **22.5**（L 70.6，C/L 0.323）。

⇒ 每张卡的 amount 由**它自己大师片的皮肤 C** 反解：
    amount = 0.4 + 0.6·(C_target − 17.2) / (27.8 − 17.2)，夹 [0.35, 1.0]

用法：
  python analysis/medium/amount_calib.py            # 只出表
  python analysis/medium/amount_calib.py --apply    # 写卡（先备份）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
import color_match as CM

# 实测杠杆（129 卡的一次扫描）
C_AT = {0.4: 17.2, 0.6: 19.4, 0.8: 22.8, 1.0: 27.8}


def solve_amount(c_target):
    xs = sorted(C_AT)
    ys = [C_AT[x] for x in xs]
    a = float(np.interp(c_target, ys, xs))
    return float(np.clip(a, 0.35, 1.0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--out', default=str(common.OUT_DEFAULT))
    args = ap.parse_args()

    cards = CM.card_features()
    rows = []
    for name, f in sorted(cards.items()):
        c = f.get('skin_C')
        if c is None:
            continue
        rows.append(dict(card=name, skin_C_target=round(float(c), 1),
                         amount=round(solve_amount(float(c)), 2)))

    from collections import Counter
    print('== amount 标定（目标 = 各卡大师片皮肤 C）')
    print('  目标 C：中位 %.1f ｜ amount：中位 %.2f ｜ 分布 %s'
          % (float(np.median([r['skin_C_target'] for r in rows])),
             float(np.median([r['amount'] for r in rows])),
             dict(sorted(Counter(r['amount'] for r in rows).items()))))

    os.makedirs(args.out, exist_ok=True)
    rep = os.path.join(args.out, 'amount_calib.json')
    with open(rep, 'w', encoding='utf-8') as fh:
        json.dump(dict(lever=C_AT, rows=rows), fh, ensure_ascii=False, indent=1)
    print('写出', rep)

    if args.apply:
        stamp = time.strftime('%Y%m%d_%H%M')
        backup = os.path.join(args.out, 'backup_cards_amount_' + stamp)
        os.makedirs(backup, exist_ok=True)
        n = 0
        for r in rows:
            p = os.path.join(str(common.CARDS), r['card'] + '.json')
            if not os.path.isfile(p):
                continue
            shutil.copy2(p, os.path.join(backup, r['card'] + '.json'))
            doc = json.load(open(p, encoding='utf-8'))
            cp = doc['medium'].setdefault('couplers', {})
            cp['amount'] = r['amount']
            with open(p, 'w', encoding='utf-8') as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=1)
            n += 1
        print('写卡 %d 张（备份 %s）' % (n, backup))


if __name__ == '__main__':
    main()
