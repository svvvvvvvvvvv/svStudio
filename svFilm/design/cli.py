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
from .lrsink import summary as xmp_summary, write_jobs
from .match import need_from_batch, report as match_report
from .stocks import registry
from .states import limit_note, regression_curve
from .batch import Batch, pilot, run_engine, run_lr, estimate
from .cards import cards as all_cards
from .texture import Style, plan_texture, warn as tex_warn
from .cards import cards as all_cards
from .flow import choose, describe


def _intent(pairs):
    """--intent 可以写多次，也可以一次写多个（逗号分隔）：--intent denser=0.6,softer=0.5"""
    it = Intent()
    for chunk in pairs or []:
        for kv in chunk.split(','):
            kv = kv.strip()
            if not kv:
                continue
            k, v = kv.split('=')
            setattr(it, k, float(v))
    return it.clamp()


def cmd_cards(a):
    tb = all_cards()
    print('卡 %d 张' % len(tb))
    for c in tb.values():
        print('  ' + str(c))


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


def cmd_run(a):
    """端到端：试点 -> 选卡 -> 整批渲 -> 量 -> LR 计划 -> XMP（**不驱动 LR**）。"""
    b = Batch.of(a.raf, a.out, limit=a.limit)
    print('批次 %s：%d 张 RAW ｜ %s' % (b.name, len(b.stems), estimate(len(b.stems))))
    it = _intent(a.intent)
    if it.describe() != '意图：无（只守不变量）':
        print(it.describe())

    print('\n[1/5] 试点（渲 %d 张，用来算需求）' % a.pilot)
    recs0 = pilot(b, n=a.pilot, log=print)
    if not recs0:
        print('试点无一有效 ⇒ 停（查 RAF / 卡）'); return

    print('\n[2/5] 选卡')
    offer = choose(recs0, it, topn=3)
    print(describe(offer))
    best = offer.best()
    if best is None:
        print('没有可用卡 ⇒ 停'); return
    print('\n选中：%s' % best.card.label())

    print('\n[3/5] 整批渲 + 量')
    recs = run_engine(b, best, limit=a.render_limit, log=print,
                      skip=list(recs0.keys()))
    # 试点那两张如果用的就是选中的卡，直接把旧测量并回来（避免漏）
    if best.card.name == sorted(all_cards())[0]:
        for k, v in recs0.items():
            recs.setdefault(k, v)
    if not recs:
        print('整批无一有效 ⇒ 停'); return

    print('\n[4/5] LR 计划 + XMP 作业单')
    out = run_lr(b, recs, it, log=print)
    print(xmp_summary(out['manifest']))

    print('\n[5/5] ①→② 回归')
    nok = 0
    for st, plan in out['plans'].items():
        import os as _os
        import numpy as _np
        from PIL import Image as _I
        pth = _os.path.join(b.sub('engine'), st + '_engine.tif')
        if not _os.path.isfile(pth):
            continue
        disp = _np.asarray(_I.open(pth).convert('RGB'), _np.float64) / 255.0
        deltas = None
        if plan.curve:
            from .curve import displacement
            img = (recs.get(st) or {}).get('img') or {}
            Ls = [img.get('p%d' % k) for k in (5, 25, 50, 75, 95)]
            ds = displacement(plan.curve, [v for v in Ls if isinstance(v, (int, float))])
            deltas = {'%d' % k: v for k, v in zip((5, 25, 50, 75, 95), ds)}
        r = regression_curve(disp, plan.curve, curve_deltas=deltas)
        nok += 1 if r.ok else 0
    print('①→② 回归：%d/%d 通过' % (nok, len(out['plans'])))
    print(limit_note())
    print('\n产物：%s（engine/ 与 xmp/）' % b.out_dir)


def cmd_states(a):
    """对一批 ① TIF：解出曲线 -> 整幅过曲线 -> 跑 ①→② 回归检查（**不用 LR**）。"""
    import os
    import numpy as np
    from PIL import Image
    recs = M.load(a.recs)
    if a.stem:
        recs = M.only(recs, a.stem)
    it = _intent(a.intent)
    print('口径 sha = %s ｜ %d 张 ｜ %s' % (M.measure_sha(), len(recs), it.describe()))
    tif_dir = a.tifs
    nok = 0
    for name, rec in recs.items():
        stem = name.replace('\\', '/').split('/')[-1].split('.')[0]
        path = os.path.join(tif_dir, name)
        if not os.path.isfile(path):
            path = os.path.join(tif_dir, stem + '.tif')
        if not os.path.isfile(path):
            continue
        plan = solve_one(rec, intent=it, log=lambda *_: None)
        disp = np.asarray(Image.open(path).convert('RGB'), np.float64) / 255.0
        deltas = None
        if plan.curve:
            # 位移要**从曲线本身**算（用 src 对 src 去 plan_curve 只会得到 0）
            from .curve import displacement
            img = rec.get('img') or {}
            Ls = [img.get('p%d' % k) for k in (5, 25, 50, 75, 95)]
            ds = displacement(plan.curve, [v for v in Ls if isinstance(v, (int, float))])
            deltas = {'%d' % k: v for k, v in zip((5, 25, 50, 75, 95), ds)}
        r = regression_curve(disp, plan.curve, curve_deltas=deltas, budget=a.budget)
        nok += 1 if r.ok else 0
        if a.show > 0:
            print('\n%s（曲线 %s 点）' % (stem, len(plan.curve or [])))
            print(r.report())
            a.show -= 1
    print('\n①→② 回归：%d/%d 通过' % (nok, len(recs)))
    print(limit_note())


def cmd_xmp(a):
    """把一批图的 LRPlan 落成 XMP 作业单（**不驱动 LR**）。"""
    recs = M.load(a.recs)
    if a.stem:
        recs = M.only(recs, a.stem)
    it = _intent(a.intent)
    plans = {}
    for name, rec in recs.items():
        stem = name.replace('\\', '/').split('/')[-1].split('.')[0]
        plans[stem] = solve_one(rec, intent=it, log=lambda *_: None)
    mf = write_jobs(plans, a.out, manifest_extra=dict(intent=it.as_dict(),
                                                      source=a.recs))
    print('口径 sha = %s ｜ %d 张 ｜ %s' % (M.measure_sha(), len(recs), it.describe()))
    print(xmp_summary(mf))
    print('输出：%s' % a.out)


def cmd_flow(a):
    recs = M.load(a.recs)
    if a.stem:
        recs = M.only(recs, a.stem)
    it = _intent(a.intent)
    print('口径 sha = %s ｜ %d 张 ｜ %s' % (M.measure_sha(), len(recs), it.describe()))
    tb = all_cards()
    print('现有卡 %d 张：%s' % (len(tb), ', '.join(c.label() for c in tb.values())))
    print()
    print(describe(choose(recs, it, topn=a.top)))


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
    sub.add_parser('cards').set_defaults(fn=cmd_cards)

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

    p = sub.add_parser('run'); p.set_defaults(fn=cmd_run)
    p.add_argument('--raf', required=True, help='RAW 目录')
    p.add_argument('--out', required=True, help='输出目录')
    p.add_argument('--limit', type=int, default=0, help='本批最多几张 RAW')
    p.add_argument('--render-limit', type=int, default=0, help='只渲前几张（试跑用）')
    p.add_argument('--pilot', type=int, default=2, help='试点渲染张数')
    p.add_argument('--intent', action='append')

    p = sub.add_parser('states'); p.set_defaults(fn=cmd_states)
    p.add_argument('recs'); p.add_argument('--tifs', required=True)
    p.add_argument('--stem', default='')
    p.add_argument('--budget', type=float, default=3.0)
    p.add_argument('--show', type=int, default=2)
    p.add_argument('--intent', action='append')

    p = sub.add_parser('xmp'); p.set_defaults(fn=cmd_xmp)
    p.add_argument('recs'); p.add_argument('--out', required=True)
    p.add_argument('--stem', default='')
    p.add_argument('--intent', action='append')

    p = sub.add_parser('flow'); p.set_defaults(fn=cmd_flow)
    p.add_argument('recs'); p.add_argument('--stem', default='')
    p.add_argument('--top', type=int, default=5)
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
