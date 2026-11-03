# -*- coding: utf-8 -*-
"""Apply the same fixed WB-style bias to every card (except xhs, already at 5200/1.0).

This is a card-level look offset, not a per-RAW solve.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

DELTA_TEMP = 723.0
DELTA_TINT = 0.056
XHS = 'xhs（整套）'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(common.OUT_DEFAULT))
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    files = sorted(glob.glob(os.path.join(str(common.CARDS), '*.json')))
    if args.apply:
        backup = os.path.join(out, 'backup_cards_before_wb_bias_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
        os.makedirs(backup)
        for f in files:
            shutil.copy2(f, os.path.join(backup, os.path.basename(f)))
        print('backup', backup)

    rows = []
    for f in files:
        c = json.load(open(f, encoding='utf-8'))
        if c['name'] == XHS:
            continue
        m = c.setdefault('medium', {})
        wb = dict(m.get('wb') or {})
        t, g = wb.get('temperature'), wb.get('tint')
        if t is None or g is None:
            print('skip no temp/tint', c['name'], wb)
            continue
        nt = round(float(t) + DELTA_TEMP, 1)
        ng = round(float(g) + DELTA_TINT, 3)
        wb['white_balance'] = 'custom'
        wb['temperature'] = nt
        wb['tint'] = ng
        m['wb'] = wb
        m['_wb_note'] = 'global fixed style bias +%.0fK / %+.3f tint vs old card value; not per-RAW solve' % (DELTA_TEMP, DELTA_TINT)
        # Some cards also carry a card-level wb that flow() prefers over medium.wb.
        cwb = dict(c.get('wb') or {})
        if cwb.get('temperature') is not None and cwb.get('tint') is not None:
            cwb['temperature'] = round(float(cwb['temperature']) + DELTA_TEMP, 1)
            cwb['tint'] = round(float(cwb['tint']) + DELTA_TINT, 3)
            c['wb'] = cwb
        rows.append((c['name'], float(t), float(g), nt, ng))
        if args.apply:
            tmp = f + '.tmp'
            json.dump(c, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            os.replace(tmp, f)
    print('cards', len(rows) + 1, 'biased', len(rows), 'mode', 'APPLY' if args.apply else 'DRY-RUN')
    for r in rows[:10]:
        print('%s  %.0f/%.3f -> %.1f/%.3f' % r)
    json.dump([{'name': r[0], 'old_temp': r[1], 'old_tint': r[2], 'new_temp': r[3], 'new_tint': r[4]} for r in rows],
              open(os.path.join(out, 'wb_bias_report.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
