# -*- coding: utf-8 -*-
"""命令行入口（设计层的"手"）。

用法：
    python -m design.cli stocks                     # 卷/纸清单 + 三组阵营
    python -m design.cli check <recs.json> --stage engine|final
    python -m design.cli plan  <recs.json> [--stem 正则] [--intent brighter=0.3]
    python -m design.cli curve <src.json> <dst.json>   # 试造曲线（看预算够不够）
"""
from __future__ import annotations

import argparse
import json
import sys

from . import measure as M
from .curve import ANCHOR_Q, plan_curve
from .engine import EnginePlan, judge_plan
from .intent import Intent
from .invariants import check_T, report
from .lr import pick_lookat, solve_exposure, solve_highlights, solve_point_color
from .stocks import registry


def cmd_stocks(_):
    reg = registry()
    print('卷 %d / 纸 %d' % (len(reg.stocks), len(reg.papers)))
    for paper, xs in reg.groups().items():
        print('\n== 配 %s ==' % paper)
        for s in xs:
            print('  ' + str(s))


def cmd_check(a):
    recs = M.load(a.recs)
    if a.stem:
        recs = M.only(recs, a.stem)
    print('口径 sha = %s ｜ %d 张' % (M.measure_sha(), len(recs)))
    bad = 0
    for name, rec in recs.items():
        r = check_T(rec, stage=a.stage)
        if not r.ok:
            bad += 1
            if bad <= a.show:
                print('\n%s' % name)
                print(report(r))
    print('\n结论：%d/%d 张破了不变量' % (bad, len(recs)))
    if bad == 0:
        print('  ✓ 全过')


def cmd_plan(a):
    recs = M.load(a.recs)
    if a.stem:
        recs = M.only(recs, a.stem)
    it = Intent()
    for kv in a.intent or []:
        k, v = kv.split('=')
        setattr(it, k, float(v))
    it.clamp()
    print('口径 sha = %s ｜ %d 张 ｜ %s' % (M.measure_sha(), len(recs), it.describe()))
    for name, rec in list(recs.items())[:a.show]:
        look = pick_lookat(rec)
        ev = solve_exposure(rec) + it.exposure_sign() * 0.15
        print('\n%s' % name)
        print('  看哪里=%s  曝光=%+.2f EV  收白=%+.0f' % (look, ev, solve_highlights(rec)))
        pc = solve_point_color(rec, target_C=a.target_c)
        if pc:
            print('  点颜色（相对当前）: scale=%+.2f shift=%+.2f' % (pc['scale'], pc['shift']))


def _stem(name):
    # 三态的文件名不同（_engine.tif / _base.tif / _xmp.tif）⇒ 按 stem 配对
    base = name.replace('\\', '/').split('/')[-1]
    return base.split('_')[0]


def _q(rec):
    """measure_all 的 img 用 p5/p25/... ，曲线那边用 '5'/'25'/... —— 这里做一次映射。"""
    img = rec.get('img') or {}
    return {'5': img.get('p5', 0.0), '25': img.get('p25', 0.0), '50': img.get('p50', 0.0),
            '75': img.get('p75', 0.0), '95': img.get('p95', 0.0)}


def cmd_curve(a):
    src = M.load(a.src)
    dst = M.load(a.dst)
    dmap = {_stem(k): v for k, v in dst.items()}
    shown = 0
    for name, rec in src.items():
        st = _stem(name)
        if st not in dmap or shown >= a.show:
            continue
        shown += 1
        sq = _q(rec)
        dq = _q(dmap[st])
        pts, info = plan_curve(sq, dq, budget=a.budget)
        print('\n%s' % name)
        print('  源 %s' % info.get('src'))
        print('  靶 %s' % info.get('dst'))
        print('  位移 %s ｜ 最大 %.2f ｜ 超预算未给 %s'
              % (info.get('delta'), info.get('max_abs_delta') or 0,
                 info.get('need_unmet') or '无'))
        print('  曲线 %s' % ('未生成（预算内无解）' if pts is None else '%d 点' % len(pts)))


def main(argv=None):
    ap = argparse.ArgumentParser(prog='design')
    sub = ap.add_subparsers(dest='cmd', required=True)

    sub.add_parser('stocks').set_defaults(fn=cmd_stocks)

    p = sub.add_parser('check'); p.set_defaults(fn=cmd_check)
    p.add_argument('recs')
    p.add_argument('--stage', default='engine', choices=['engine', 'final'])
    p.add_argument('--stem', default='')
    p.add_argument('--show', type=int, default=3)

    p = sub.add_parser('plan'); p.set_defaults(fn=cmd_plan)
    p.add_argument('recs')
    p.add_argument('--stem', default='')
    p.add_argument('--show', type=int, default=3)
    p.add_argument('--target-c', type=float, default=None)
    p.add_argument('--intent', action='append',
                   help='例: brighter=0.3 / denser=-0.2')

    p = sub.add_parser('curve'); p.set_defaults(fn=cmd_curve)
    p.add_argument('src'); p.add_argument('dst')
    p.add_argument('--budget', type=float, default=3.0)
    p.add_argument('--show', type=int, default=3)

    a = ap.parse_args(argv)
    a.fn(a)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
