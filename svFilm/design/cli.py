# -*- coding: utf-8 -*-
"""命令行入口（设计层的"手"）。

    python -m design.cli stocks                       # 卷/纸清单 + 三组阵营 + 逐卷规格
    python -m design.cli check  <recs.json> --stage engine|final [--stem 正则]
    python -m design.cli plan   <recs.json> [--stem 正则] [--intent k=v ...]
    python -m design.cli curve  <recs.json> [--stem 正则] [--intent k=v ...]
    python -m design.cli match  <recs.json> [--intent k=v ...] [--top 6]
    python -m design.cli texture <stock> [--long 1080] [--format 36] [--diffusion off]
"""
from __future__ import annotations

import argparse

from . import measure as M
from .intent import Intent
from .invariants import check_T, report
from .lr import solve_curve, solve_one
from .match import need_from_batch, report as match_report
from .stocks import registry
from .texture import Style, plan_texture, warn as tex_warn


def _intent(pairs):
    it = Intent()
    for kv in pairs or []:
        k, v = kv.split('=')
        setattr(it, k, float(v))
    return it.clamp()


def cmd_stocks(a):
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
    it = _intent(a.intent)
    print('口径 sha = %s ｜ %d 张 ｜ %s' % (M.measure_sha(), len(recs), it.describe()))
    for name, rec in list(recs.items())[:a.show]:
        p = solve_one(rec, intent=it)
        print('\n%s' % name)
        print('  看哪里=%s  曝光=%+.2f EV  收白=%+.0f  饱和=%+.0f'
              % (p.notes.get('lookat'), p.exposure_ev, p.highlights, p.saturation))
        print('  曲线：%s' % p.notes.get('curve'))


def cmd_curve(a):
    recs = M.load(a.recs)
    if a.stem:
        recs = M.only(recs, a.stem)
    it = _intent(a.intent)
    for name, rec in list(recs.items())[:a.show]:
        pts, info = solve_curve(rec, intent=it, budget=a.budget)
        print('\n%s' % name)
        print('  分位 %s' % {k: round(v, 1) for k, v in (info.get('src') or {}).items()})
        print('  修正 %s ｜ 最大位移 %.2f'
              % (info.get('fixed') or info.get('why'), info.get('max_abs_delta') or 0))
        if info.get('unmet'):
            print('  ⚠ 预算内修不掉：%s ⇒ 这不是曲线的活，回引擎要余量（T0）'
                  % info['unmet'])
        print('  曲线 %s' % ('不需要' if pts is None else '%d 点' % len(pts)))


def cmd_match(a):
    recs = M.load(a.recs)
    if a.stem:
        recs = M.only(recs, a.stem)
    it = _intent(a.intent)
    print('口径 sha = %s ｜ %d 张' % (M.measure_sha(), len(recs)))
    print(match_report(registry(), need_from_batch(recs, it), topn=a.top))


def cmd_texture(a):
    st = Style(format_mm=a.format, diffusion=a.diffusion)
    p = plan_texture(a.stock, a.paper or '', long_side=a.long, style=st)
    print('== %s ｜ 长边 %d ==' % (a.stock, a.long))
    print(p)
    print()
    print(tex_warn(p, long_side=a.long))


def main(argv=None):
    ap = argparse.ArgumentParser(prog='design')
    sub = ap.add_subparsers(dest='cmd', required=True)

    sub.add_parser('stocks').set_defaults(fn=cmd_stocks)

    p = sub.add_parser('check'); p.set_defaults(fn=cmd_check)
    p.add_argument('recs'); p.add_argument('--stage', default='engine',
                                           choices=['engine', 'final'])
    p.add_argument('--stem', default=''); p.add_argument('--show', type=int, default=3)

    p = sub.add_parser('plan'); p.set_defaults(fn=cmd_plan)
    p.add_argument('recs'); p.add_argument('--stem', default='')
    p.add_argument('--show', type=int, default=3)
    p.add_argument('--intent', action='append', help='例: brighter=0.3')

    p = sub.add_parser('curve'); p.set_defaults(fn=cmd_curve)
    p.add_argument('recs'); p.add_argument('--stem', default='')
    p.add_argument('--budget', type=float, default=3.0)
    p.add_argument('--show', type=int, default=3)
    p.add_argument('--intent', action='append')

    p = sub.add_parser('match'); p.set_defaults(fn=cmd_match)
    p.add_argument('recs'); p.add_argument('--stem', default='')
    p.add_argument('--top', type=int, default=6)
    p.add_argument('--intent', action='append')

    p = sub.add_parser('texture'); p.set_defaults(fn=cmd_texture)
    p.add_argument('stock'); p.add_argument('--paper', default='')
    p.add_argument('--long', type=int, default=1080)
    p.add_argument('--format', type=float, default=36.0)
    p.add_argument('--diffusion', default='off')

    a = ap.parse_args(argv)
    a.fn(a)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
