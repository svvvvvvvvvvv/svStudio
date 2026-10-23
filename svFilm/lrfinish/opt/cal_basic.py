# -*- coding: utf-8 -*-
r"""Basic 六旋钮的响应标定：每根 +Δ 一档，量脸/光比/贴白/压黑/分位的变化。

产物：calibration/basic_gain.json
    {image, base:{...}, steps:{key:{delta, d:{metric:...}}}}
后续 basic_qp.py 用它做 6 维有界最小二乘，不再逐旋钮割线。

用法：
  python opt/cal_basic.py --tif <底片.tif> --out <json>
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths
paths.ensure_import_path()
import basic                                          # noqa: E402
import lr_boot                                        # noqa: E402
import time                                           # noqa: E402

STEPS = {
    'Exposure2012': +0.50,
    'Contrast2012': +30.0,
    'Highlights2012': -50.0,
    'Shadows2012': +50.0,
    'Whites2012': +30.0,
    'Blacks2012': -30.0,
}
METRICS = ('p50', 'span', 'white', 'black', 'p90', 'p10', 'p99')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tif', required=True)
    ap.add_argument('--out', default=str(paths.ROOT / 'calibration' / 'basic_gain.json'))
    ap.add_argument('--edge', type=int, default=1024)
    a = ap.parse_args()

    if lr_boot.lr_pids():
        lr_boot.kill_lr(); time.sleep(3)
    print('日志清理: %s ｜ 锁: %s' % (lr_boot.clear_console_log(), lr_boot.clear_stale_lock()))
    lr_boot.launch()
    ok, msg = lr_boot.wait_bridge(150)
    print('桥：%s %s' % ('就绪' if ok else '未就绪', msg))
    if not ok:
        return

    src = a.tif
    basic.run('SELECT ' + src, timeout=60)
    basic.run('RESET ' + src, timeout=60)
    tag = os.path.join(os.path.dirname(a.out), '_cal_basic')
    _, base, _ = basic._state(src, dict(basic.ZERO), os.path.join(tag, 'base'), a.edge)
    if not base:
        raise SystemExit('基准导出失败')
    out = dict(image=os.path.basename(src),
               base={k: float(base[k]) for k in METRICS if k in base},
               steps={})
    for key, step in STEPS.items():
        vals = dict(basic.ZERO)
        vals[key] = basic.clip_key(key, step)
        _, m, f = basic._state(src, vals, os.path.join(tag, key), a.edge)
        if not m:
            print('  %s 导出失败' % key); continue
        d = {k: float(m[k] - base[k]) for k in METRICS if k in m and k in base}
        out['steps'][key] = dict(delta=step, d=d)
        print('%-16s %+6.1f  ' % (key, step)
              + ' '.join('%s%+.2f' % (k, d[k]) for k in ('p50', 'span', 'white', 'black')))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(out, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('-> %s' % a.out)


if __name__ == '__main__':
    main()
