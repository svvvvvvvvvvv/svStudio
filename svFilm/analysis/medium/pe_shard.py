# -*- coding: utf-8 -*-
"""一张分片：按列表重建 PE 表。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--long-side', type=int, default=1080)
    ap.add_argument('--log', default='')
    args = ap.parse_args()

    sys.path.insert(0, str(common.SVFILM))
    from design import chart_calib as CC
    from design.card_render import medium_of

    files = [l.strip() for l in open(args.list, encoding='utf-8') if l.strip()]
    logf = open(args.log, 'a', encoding='utf-8') if args.log else None
    for f in files:
        card = json.load(open(f, encoding='utf-8'))
        name = card['name']
        out = os.path.join(args.out, '%s.json' % name)
        t0 = time.time()
        try:
            med = medium_of(card)
            CC.render_table(med, out_path=out, long_side=args.long_side,
                            wedge_mode='ramp',
                            pe_values=[0.0625, 0.125, 0.25, 0.5, 1.0, 2.0, 4.0])
            doc = json.load(open(out, encoding='utf-8'))
            doc['card'] = name
            with open(out, 'w', encoding='utf-8') as fh:
                json.dump(doc, fh, ensure_ascii=False)
            msg = '%-30s %.1fs' % (name[:30], time.time() - t0)
        except Exception as e:                             # noqa: BLE001
            msg = '%-30s FAIL %s' % (name[:30], e)
        print(msg, flush=True)
        if logf:
            logf.write(msg + '\n')
            logf.flush()
    if logf:
        logf.close()


if __name__ == '__main__':
    main()
