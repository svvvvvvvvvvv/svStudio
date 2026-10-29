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
import os

from . import measure as M
from .intent import Intent
from .invariants import check_T, report
from .lr import solve_curve, solve_one
from .lrsink import apply_and_export, summary as xmp_summary, write_jobs
from .match import need_from_batch, report as match_report
from .stocks import registry
from .feasibility import gap_table, verdict
from .refcards import profile_batch, rank_refs, refcards
from .states import limit_note, regression_curve
from .batch import Batch, pilot, run_engine, run_lr, run_final, build_review, estimate
from .cards import cards as all_cards
from .texture import Style, plan_texture, warn as tex_warn
from .cards import cards as all_cards
from .flow import Choice, choose, describe


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


def cmd_refs(a):
    """在**大师的每一套**里找"这批最像哪一套"（64 张参照卡，不是 3 张生产卡）。"""
    recs = M.load(a.recs)
    if a.stem:
        recs = M.only(recs, a.stem)
    tb = profile_batch(recs)
    print('口径 sha = %s ｜ 本批 %d 张' % (M.measure_sha(), len(recs)))
    print('本批画像: 脸L=%.1f 脸C=%.1f 脸h=%.1f p50=%.1f p95=%.1f'
          % (tb['L'], tb['C'], tb['h'], tb['p50'], tb['p95']))
    print()
    refs = refcards()
    print('参照卡 %d 张（大师的每一套一张）' % len(refs))
    print('  分数   套                                  张数  差在哪')
    for h in rank_refs(tb, refs)[:a.top]:
        print('  ' + str(h))
    print()
    print('  ⚠ 这只是"最像哪一套"，不是靶；参照卡只有画像、没有配方。')


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
    if a.headroom_dir:
        # 引擎渲完写的 sidecar：这张底片还能抬几档（没有它，预算判断就只剩白端一道）
        import glob
        for name, rec in recs.items():
            st = name.split('_')[0]
            p = os.path.join(a.headroom_dir, st + '_headroom.json')
            if os.path.isfile(p):
                import json as _j
                try:
                    j = _j.load(open(p, encoding='utf-8'))
                    rec['headroom_ev'] = float(j['headroom_ev'])
                    # ★ 余量可不可信，一起带进去（降白端换来的余量是零和，见 flow 常数区）
                    if 'valid' in j:
                        rec['headroom_valid'] = bool(j['valid'])
                        rec['headroom_why'] = str(j.get('why') or '')
                except Exception:
                    pass
    if a.stem:
        recs = M.only(recs, a.stem)
    else:
        # 默认只看**引擎那一态**：`_sim.png` 是仿真预览，混进来会把同一张图算两遍
        recs = M.only(recs, r'_engine\.tif$')
    it = _intent(a.intent)
    print('口径 sha = %s ｜ %d 张 ｜ %s' % (M.measure_sha(), len(recs), it.describe()))
    for name, rec in list(recs.items())[:a.show]:
        p = solve_one(rec, intent=it)
        print('\n%s' % name)
        print('  看哪里=%s' % p.notes.get('lookat'))
        print('  曝光  %+.2f EV ｜ %s' % (p.exposure_ev, p.notes.get('exposure', '')))
        if p.notes.get('exposure_debt'):
            print('  欠账  %s' % p.notes['exposure_debt'])
        if p.notes.get('after_ev'):
            print('  ①\'   %s' % p.notes['after_ev'])
        print('  收白  %+.0f ｜ %s' % (p.highlights, p.notes.get('highlights') or '不动'))
        if p.notes.get('highlight_verify'):
            print('  待验  %s' % p.notes['highlight_verify'])
        if p.notes.get('highlight_debt'):
            print('  欠账  %s' % p.notes['highlight_debt'])
        print('  曲线  %s' % p.notes.get('curve'))


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

    print('\n[2/5] 这批像哪一套（参照卡 = 大师的每一套）')
    tb = profile_batch(recs0)
    refs = refcards()
    hits = rank_refs(tb, refs)
    print('  本批画像：脸L=%.1f 脸C=%.1f 脸h=%.1f p50=%.1f p95=%.1f（%d 张试点）'
          % (tb['L'], tb['C'], tb['h'], tb['p50'], tb['p95'], len(recs0)))
    print('  %d 张参照卡里最像的：' % len(refs))
    for h in hits[:5]:
        print('    ' + str(h))

    print('\n[3/5] 现有生产卡（能渲的）')
    offer = choose(recs0, it, topn=3)
    if getattr(a, 'card', ''):
        # 手动指定卡（不靠匹配排序）：验证某张卡的参数时用
        import types
        from .cards import cards as _cards
        from .texture import plan_texture as _plan_tex
        cc = _cards().get(a.card)
        if cc is None:
            print('没有这张卡：%s（有：%s）' % (a.card, ' / '.join(sorted(_cards()))))
            return
        offer.choices.insert(0, Choice(
            hit=types.SimpleNamespace(card=cc, score=0.0, why='手动指定，不经匹配'),
            texture=_plan_tex(cc.stock, cc.paper, long_side=1080)))
        print('  ★ 手动指定：%s（匹配排序已让位）' % cc.label())
    print(describe(offer))

    print('\n[3b] 可行性：最近那套参照卡的缺口，关得掉吗？')
    rows = gap_table(tb, hits[0].ref.profile, headroom_ev=0.15)
    for r in rows:
        print(str(r))
    print('  ' + verdict(rows).replace('\n', '\n  '))
    best = offer.best()
    if best is None:
        print('没有可用卡 ⇒ 停'); return
    # ★ 选卡这一步要**停下来给人看**：权重、理由、可渲性都在上面
    if not getattr(a, 'yes', False):
        print('\n以上是匹配结果（权重与理由）。确认要用「%s」继续？' % best.card.label())
        print('  加 --yes 才继续渲整批；或改 --card 指定另一张卡。')
        return
    print('\n选中：%s' % best.card.label())

    print('\n[4/5] 整批渲 + 量')
    recs = run_engine(b, best, limit=a.render_limit, log=print,
                      skip=list(recs0.keys()))
    # 试点那两张如果用的就是选中的卡，直接把旧测量并回来（避免漏）
    if best.card.name == sorted(all_cards())[0]:
        for k, v in recs0.items():
            recs.setdefault(k, v)
    if not recs:
        print('整批无一有效 ⇒ 停'); return

    print('\n[5/5] LR 计划 + XMP 作业单')
    out = run_lr(b, recs, it, log=print)
    print(xmp_summary(out['manifest']))

    reviewed = None
    if a.export:
        print('\n[4b/5] 产品阶段：LR 出成片 ③ → 量 → 验收（唯一驱动 LR 的一步）')
        try:
            o3 = run_final(b, recs, out['plans'], rounds=a.rounds, log=print)
            print(' ③ 成片 %d 张（%d 轮）' % (len(o3['recs3']), o3['rounds']))
            reviewed = build_review(b, recs, out['plans'], o3['recs3'], log=print)
            print(' 验收：硬门（脸）过 %d/%d 张' % (reviewed['ok'], reviewed['n']))
            print(' 目检：%s' % reviewed['html'])
        except Exception as e:
            print(' ! LR 出片失败：%s' % str(e)[:160])
            print('   （LR 没启动？桥不通只会挡"出片"这一步；算值与作业单不受影响）')

    print('\n[6/6] ①→② 曲线回归（不用 LR 的便宜检查）')
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


def cmd_review(a):
    """验收 + 目检页：拿 ① 与 ③ 的**测量**对比，出一页能看的图。

    不驱动 LR、不改参数。③ 的量从哪来都行（真出片 / 历史 run 的测量文件）。
    参数优先读 `--manifest`（LR 作业单里记的实际值）；没有就按当前代码重算一遍。
    """
    import json
    import types
    from .review import by_stem, make_review

    r1 = M.load(a.recs1)
    r3 = M.load(a.recs3)
    if a.stem:
        r1, r3 = M.only(r1, a.stem), M.only(r3, a.stem)

    plans = {}
    if a.manifest and os.path.isfile(a.manifest):
        man = json.load(open(a.manifest, encoding='utf-8'))
        for it in man.get('items') or []:
            plans[it['stem']] = types.SimpleNamespace(
                exposure_ev=it.get('exposure_ev') or 0.0,
                highlights=it.get('highlights') or 0.0,
                saturation=it.get('saturation') or 0.0,
                point_color=it.get('point_color'),
                curve=None)
        print('参数来源：%s（%d 张）' % (os.path.basename(a.manifest), len(plans)))
    if not plans:
        for st, rec in by_stem(r1).items():
            plans[st] = solve_one(rec, log=lambda *_: None)
        print('参数来源：按当前代码重算（%d 张）' % len(plans))

    out = make_review(r1, r3, plans, a.out, tif_1_dir=a.tif1, tif_3_dir=a.tif3,
                      title=a.title, ev_known=bool(a.manifest), log=print)
    print('\n验收：硬门（脸）过 %d/%d' % (out['ok'], out['n']))
    for r in out['rows']:
        print('  %-10s %s' % (r['stem'], r['verdict']))
        print('             %s%s' % (r['values'], r['note']))
    print('\n目检页：%s' % out['html'])


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

    p = sub.add_parser('refs'); p.set_defaults(fn=cmd_refs)
    p.add_argument('recs'); p.add_argument('--stem', default='')
    p.add_argument('--top', type=int, default=8)

    p = sub.add_parser('check'); p.set_defaults(fn=cmd_check)
    p.add_argument('recs'); p.add_argument('--stage', default='engine',
                                           choices=['engine', 'final'])
    p.add_argument('--stem', default=''); p.add_argument('--show', type=int, default=3)

    p = sub.add_parser('plan'); p.set_defaults(fn=cmd_plan)
    p.add_argument('recs'); p.add_argument('--stem', default='')
    p.add_argument('--show', type=int, default=3)
    p.add_argument('--headroom-dir', default='',
                   help='引擎片目录（读 *_headroom.json 补"还能抬几档"）')
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
    p.add_argument('--yes', action='store_true', help='选卡后不再等确认')
    p.add_argument('--card', default='', help='手动指定卡名（跳过匹配排序）')
    p.add_argument('--export', action='store_true', help='用 LR 导出 ③（要 LR 在跑）')
    p.add_argument('--rounds', type=int, default=2, help='③ 出片最多几轮（收白按脸进退）')

    p = sub.add_parser('review'); p.set_defaults(fn=cmd_review)
    p.add_argument('recs1', help='① 的测量（measure_all 输出）')
    p.add_argument('recs3', help='③ 的测量')
    p.add_argument('--out', required=True, help='目检页输出目录')
    p.add_argument('--tif1', default='', help='① 的 tif 目录（出图用）')
    p.add_argument('--tif3', default='', help='③ 的 tif 目录（出图用）')
    p.add_argument('--manifest', default='', help='LR 作业单 manifest.json（读实际参数）')
    p.add_argument('--stem', default='')
    p.add_argument('--title', default='成片目检')

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
