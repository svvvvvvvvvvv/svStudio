# -*- coding: utf-8 -*-
"""Card-level WB style bias from each card's master WB cast direction.

Not a decode inversion: base is fixed; cast only sets a small direction offset.
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

BASE_TEMP = 5353.0
BASE_TINT = 1.006
TEMP_PER_B = 40.0      # +1 b* (yellow) -> +40K
TINT_PER_A = -0.010    # -1 a* (green) -> +0.010 tint


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(common.OUT_DEFAULT))
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    files = sorted(glob.glob(os.path.join(str(common.CARDS), '*.json')))
    if args.apply:
        backup = os.path.join(out, 'backup_cards_before_wb_cast_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
        os.makedirs(backup)
        for f in files:
            shutil.copy2(f, os.path.join(backup, os.path.basename(f)))
        print('backup', backup)

    rows = []
    for f in files:
        c = json.load(open(f, encoding='utf-8'))
        cast = (c.get('wb') or {}).get('cast') or {}
        a = float(cast.get('a') or 0.0)
        b = float(cast.get('b') or 0.0)
        temp = round(BASE_TEMP + TEMP_PER_B * b, 1)
        tint = round(BASE_TINT + TINT_PER_A * a, 3)
        temp = max(5000.0, min(6000.0, temp))
        tint = max(0.96, min(1.08, tint))
        m = c.setdefault('medium', {})
        m['wb'] = {'white_balance': 'custom', 'temperature': temp, 'tint': tint}
        m['_wb_note'] = ('master-cast style bias: base %.0fK/%.3f; '
                         'a%+.1f b%+.1f -> %0.1fK/%0.3f; direction only, not inversion'
                         % (BASE_TEMP, BASE_TINT, a, b, temp, tint))
        cwb = dict(c.get('wb') or {})
        if cwb.get('temperature') is not None and cwb.get('tint') is not None:
            cwb['temperature'] = temp
            cwb['tint'] = tint
            c['wb'] = cwb
        rows.append({'name': c['name'], 'cast_a': a, 'cast_b': b,
                     'temp': temp, 'tint': tint})
        if args.apply:
            tmp = f + '.tmp'
            json.dump(c, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            os.replace(tmp, f)
    json.dump(rows, open(os.path.join(out, 'wb_cast_report.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('cards', len(rows), 'mode', 'APPLY' if args.apply else 'DRY-RUN')
    for r in rows[:12]:
        print(r)


if __name__ == '__main__':
    main()
