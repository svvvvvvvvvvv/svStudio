# -*- coding: utf-8 -*-
"""并行重建 64 卡的 PE 表（按卡分片，每片一个进程）。

用法：python analysis/medium/pe_par.py --shards 4 --out <dir> --long-side 1080
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shards', type=int, default=4)
    ap.add_argument('--out', default=str(common.OUT_DEFAULT / 'pe_tables'))
    ap.add_argument('--long-side', type=int, default=1080)
    args = ap.parse_args()

    cards = sorted(glob.glob(str(common.CARDS / '*.json')))
    os.makedirs(args.out, exist_ok=True)
    here = os.path.dirname(os.path.abspath(__file__))
    procs = []
    for i in range(args.shards):
        sub = cards[i::args.shards]
        listf = os.path.join(args.out, '_shard_%d.txt' % i)
        with open(listf, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(sub))
        logf = os.path.join(args.out, '_shard_%d.log' % i)
        cmd = [sys.executable, os.path.join(here, 'pe_shard.py'),
               '--list', listf, '--out', args.out, '--long-side', str(args.long_side),
               '--log', logf]
        procs.append(subprocess.Popen(cmd))
        print('shard %d: %d 卡' % (i, len(sub)))
    for p in procs:
        p.wait()
    print('全部完成')


if __name__ == '__main__':
    main()
