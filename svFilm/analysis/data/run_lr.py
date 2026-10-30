# -*- coding: utf-8 -*-
"""执行：卡 004 的 LR 计划 → 导出 ③ → 量 ③（§56 步 6~7 的前半）。

用法：& $py -X utf8 -u run_lr.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, r'E:\svStudio\svFilm')

OUT = r'E:\ds\photo\_run_1030_card004'
ENGINE = r'E:\ds\photo\_run_1029_tex2\engine'     # 与卡 004 同介质（卡38 → portra400+endura）
CARDS = r'E:\ds\photo\_corpus\kurai\full\cards_v3.json'
RECS = r'E:\ds\photo\_run_1029_tex2\m_v3b.json'
CARD = '004_鹿井小渔_n6ab1e755'


def main() -> int:
    from design.lr_exec import run
    from design.lr_solve import plan_batch, summary

    d = json.load(open(CARDS, encoding='utf-8'))
    card = next(c for c in d['cards'] if c['name'] == CARD)
    recs = json.load(open(RECS, encoding='utf-8'))['per_image']
    if isinstance(recs, list):
        recs = {os.path.basename(r['file']).replace('_engine.tif', ''): r for r in recs}
    plans = plan_batch(recs, card)
    print(summary(plans, CARD))
    got = run(plans, ENGINE, os.path.join(OUT, 'v11'), log=print)
    print('\n③ 出片 %d 张 → %s' % (len(got), os.path.join(OUT, 'final')))
    with open(os.path.join(OUT, 'v4', 'lr_result.json'), 'w', encoding='utf-8') as fh:
        json.dump(got, fh, ensure_ascii=False, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
