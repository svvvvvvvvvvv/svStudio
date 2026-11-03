# -*- coding: utf-8 -*-
"""命令行入口（设计层的"手"）。

    python -m design.cli stocks                       # 卷/纸清单
    python -m design.cli cards                        # 卡清单
    python -m design.cli match-jpg --raf <RAW目录> --out <输出目录>
    python -m design.cli run --raf <RAW目录> --out <输出目录> [--content a.json]
    python -m design.cli run --raf <RAW目录> --out <输出目录> --card <卡名> [--hero <样片>] [--export]
    python -m design.cli page --out <输出目录>          # 重出内容匹配页（不渲不量）
    python -m design.cli xmp  <①测量.json> --card <卡名> --out <输出目录>
    python -m design.cli review <①测量.json> <③测量.json> --out <输出目录> --manifest <xmp/manifest.json>

流程（`设计真源.md` §3）：
    机内 JPG 匹配 → 人拍板卡/hero → 渲一次 ① → 逐片 peer → LR-B 计划
    → 写 XMP（可选驱动 LR 出 ③）→ 目检页（人目检是唯一验收）
"""
from __future__ import annotations

import argparse
import json
import os
import types

from .batch import Batch, run_engine, measure_tif, measure_engine_dir, build_review, estimate
from .batch_guard import build as build_guard
from .card_content import describe as describe_content
from .contentmatch import match_jpgs
from .lr_exec import run as lr_run
from .lr_solve import plan_batch, summary as lr_summary
from .matchcard import card_samples, cards as match_cards, raw_card
from .matchpage import make_content_page
from .review import make_review
from .lrsink import write_jobs, summary as xmp_summary
from .stocks import registry


# ---------------------------------------------------------------- 卡/卷

def cmd_cards(a):
    cs = match_cards()
    print('卡 %d 张（一张 = 一个大师目录：介质 + 点值 + WB + PE + 样片）' % len(cs))
    for c in cs:
        print('  %-30s %-22s %-22s n=%-3d' % (c.name, c.stock, c.paper, c.n))


def cmd_stocks(a):
    reg = registry()
    print('卷 %d / 纸 %d' % (len(reg.stocks), len(reg.papers)))
    for paper, xs in reg.groups().items():
        print('\n== 配 %s ==' % paper)
        for s in xs:
            print('  ' + str(s))


# ---------------------------------------------------------------- 匹配

def cmd_match_jpg(a):
    """机内 JPG 内容匹配：**不渲 ①′**，只读 RAW 内嵌 JPG。"""
    res = match_jpgs(a.raf, a.out, content_path=a.content, topn=a.top, limit=a.limit)
    print('\n【内容匹配】前 %d 个候选（看样片定，别只看分数）：' % len(res['hits']))
    print(describe_content(res['hits']))
    print('\n这批内容：%s' % res['batch'].get('evidence'))
    print('内容匹配页：%s' % res['page'])
    print('看完回我：--card <名字> --hero <样片>')


def cmd_page(a):
    """只重出内容匹配页：不渲、不量、不驱动 LR。"""
    mp = os.path.join(a.out, 'match')
    doc_p = os.path.join(mp, 'content_match.json')
    if not os.path.isfile(doc_p):
        print('没有内容匹配记录：%s' % doc_p)
        return
    doc = json.load(open(doc_p, encoding='utf-8'))
    jpgs = {}
    for f in sorted(os.listdir(os.path.join(a.out, 'jpg'))):
        if f.lower().endswith(('.jpg', '.jpeg')):
            jpgs[os.path.splitext(f)[0]] = os.path.join(a.out, 'jpg', f)
    from . import card_content
    page = make_content_page(doc.get('batch_content') or {}, doc.get('hits') or [],
                             jpgs, a.out, title=a.title or doc.get('batch', '内容匹配'),
                             all_cards=sorted(card_content.load().keys()))
    print('内容匹配页：%s' % page)


# ---------------------------------------------------------------- 端到端

def _resolve_hero(card_name: str, hero: str) -> str:
    samples = [os.path.normpath(p) for p in card_samples(card_name)]
    if not samples:
        return ''
    hp = os.path.normpath(hero)
    if os.path.isabs(hp):
        return hp if hp in samples else ''
    hits = [p for p in samples if os.path.basename(p) == hp]
    return hits[0] if len(hits) == 1 else ''


def _hero_target(hero: str):
    """hero 样片 → 与 peer 同结构的关系目标；不拿绝对 p50 当靶。"""
    if not hero or not os.path.isfile(hero):
        return None
    try:
        from .measure_all import measure_one
        from .peer import target_from_rec
        return target_from_rec(measure_one(hero))
    except Exception as ex:                                # noqa: BLE001
        print('  ! hero 测量失败，退卡目标：%s' % str(ex)[:120])
        return None


def _jpg_measures(b, content_path: str = '', log=print) -> dict:
    """JPG 测量：优先复用 `match/jpg_measure.json`；否则抽 JPG 再量。不渲 ①。"""
    from .contentmatch import find_raws, extract_jpgs, measure_jpgs
    cache = os.path.join(b.out_dir, 'match', 'jpg_measure.json')
    if os.path.isfile(cache):
        try:
            d = json.load(open(cache, encoding='utf-8'))
            rows = d.get('per_image') if isinstance(d, dict) else d
            if rows:
                log('  JPG 测量 ← %s（%d 张）' % (cache, len(rows)))
                return rows
        except Exception:                                  # noqa: BLE001
            pass
    raws = find_raws(b.raf_dir)
    jpgs = extract_jpgs(raws, b.out_dir, log=log)
    measures = measure_jpgs(jpgs, log=log)
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    tmp = cache + '.tmp'
    json.dump({'per_image': measures}, open(tmp, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    os.replace(tmp, cache)
    return measures


def _plans_from_manifest(path: str) -> dict:
    """XMP manifest → {stem: 轻量 plan}（只给 review 页显示用）。"""
    doc = json.load(open(path, encoding='utf-8'))
    out = {}
    for it in doc.get('items') or []:
        wb = it.get('wb') or {}
        basic = it.get('basic') or {}
        pc = it.get('point_color') or {}
        loc = it.get('local') or {}
        out[it['stem']] = types.SimpleNamespace(
            wb=types.SimpleNamespace(card_white=wb.get('card_white'),
                                     note=wb.get('note') or ''),
            exposure_ev=basic.get('exposure_ev') or 0.0,
            contrast=basic.get('contrast') or 0.0,
            highlights=basic.get('highlights') or 0.0,
            shadows=basic.get('shadows') or 0.0,
            whites=basic.get('whites') or 0.0,
            blacks=basic.get('blacks') or 0.0,
            point_color=types.SimpleNamespace(**pc) if pc else None,
            local=types.SimpleNamespace(**loc) if loc else None,
            curve=[None] * int(it.get('curve_points') or 0),
            target=it.get('target') or {},
            card_dev=it.get('card_dev') or {})
    return out


def cmd_run(a):
    """端到端：匹配（或指定卡）→ 渲一次 ① → peer → LR 计划 →（可选）③。"""
    b = Batch.of(a.raf, a.out, limit=a.limit)
    print('批次 %s：%d 张 RAW ｜ %s' % (b.name, len(b.stems), estimate(len(b.stems))))
    by_name = {c.name: c for c in match_cards()}

    if not a.card:
        # 匹配 A：机内 JPG，不渲任何东西；停在等人拍板
        print('\n[1/3] 机内 JPG 内容匹配（不跑 ①′）')
        try:
            res = match_jpgs(a.raf, b.out_dir, content_path=a.content,
                             topn=5, limit=a.limit, log=print)
        except Exception as ex:                            # noqa: BLE001
            print('  内容匹配失败：%s' % ex)
            print('  ⇒ 停在这里，不退回基线渲染（设计：不单独跑一次）')
            return
        print('\n[2/3] 这批内容：%s' % res['batch'].get('evidence'))
        print(describe_content(res['hits']))
        print('\n[3/3] **停在这里等你拍板**（不自动往下走）')
        print('  内容匹配页：%s' % res['page'])
        print('  看完回我：--card <名字> --hero <样片>')
        return

    card_mc = by_name.get(a.card)
    if card_mc is None:
        # 显式指定 thin 卡：允许跑，但不进自动匹配候选。
        from types import SimpleNamespace
        r = raw_card(a.card)
        if r:
            sim = (r.get('medium') or {}).get('simulation') or {}
            card_mc = SimpleNamespace(
                name=r['name'],
                stock=str(sim.get('film_stock') or r.get('stock') or ''),
                paper=str(sim.get('print_paper') or r.get('paper') or ''),
                wb=dict(r.get('wb') or {}),
                corpus=str(r.get('corpus') or ''),
                thin=True)
            print('  ! 卡是 thin（不参与自动匹配）；显式指定，允许跑')
    if card_mc is None:
        print('没有这张卡：%s（有 %d 张）' % (a.card, len(by_name)))
        return
    print('\n卡：%s ｜ 介质 %s + %s' % (card_mc.name, card_mc.stock, card_mc.paper))
    b.card = card_mc.name
    if a.hero:
        hero = _resolve_hero(b.card, a.hero)
        if not hero:
            print('hero 不属于这张卡：%s（例如 %s）'
                  % (a.hero, '、'.join(os.path.basename(p)
                                       for p in card_samples(b.card)[:3])))
            return
        b.hero = hero
        print('hero：%s（目检参考 + peer 不足时的关系兜底）' % os.path.basename(hero))
    print('批次元数据：%s' % b.write_meta())

    print('\n[1/4] 机内 JPG 测量 + peer target（不渲 ①）')
    measures = _jpg_measures(b, content_path=a.content, log=print)
    from . import peer_ratio, chart_calib
    targets = peer_ratio.build(measures, card_mc.name, log=print)
    got_t = sum(1 for v in targets.values() if (v.get('peer_q') or {}).get('p50') is not None)
    print('  peer：%d/%d 张有 target' % (got_t, len(targets)))
    pt = os.path.join(b.out_dir, 'peer_targets.json')
    tmp = pt + '.tmp'
    json.dump({'card': card_mc.name, 'per_image': targets},
              open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    os.replace(tmp, pt)
    print('  target：%s' % pt)
    try:
        _st, _pp = (getattr(a, 'stock', '') or ''), (getattr(a, 'paper', '') or '')
        _ovr = ''
        if _st or _pp:
            _tdir = os.environ.get('SV_PE_TABLE_DIR',
                                   r'E:\ds\photo\_paper_matrix\_tables')
            _cand = os.path.join(_tdir, '%s__%s.json'
                                 % (_st or card_mc.stock, _pp or card_mc.paper))
            if os.path.isfile(_cand):
                _ovr = _cand
        if _ovr:
            table = chart_calib.load_table(_ovr)
            print('  介质 PE 表（覆盖卷纸）：%s' % _ovr)
        else:
            table = chart_calib.load_card_table(card_mc.name)
            print('  卡级 PE 表：%s' % chart_calib.card_table_path(card_mc.name))
    except Exception as e:                                 # noqa: BLE001
        print('  ! %s' % e)
        return
    # 目标现在是「一张 peer 的直方图」，整条传给引擎（不再是单个 p50 数）
    flat = {k: v for k, v in targets.items()
            if (v.get('peer_q') or {}).get('p50') is not None}
    if not flat:
        print('  ! 没有 peer target ⇒ 停（先跑 peer）')
        return

    print('\n[2/4] 渲 ①（一次；卡的介质 + 逐图 PE → peer target；不读脸）')
    if a.reuse:
        from .card_render import medium_of
        m = medium_of(card_mc)
        sim = m.get('simulation') or {}
        cw = card_mc.wb or {}
        mw = m.get('wb') or {}
        use = cw if (cw.get('temperature') is not None
                     and cw.get('tint') is not None) else mw
        expect = dict(card=card_mc.name,
                      medium='%s + %s' % (sim.get('film_stock'), sim.get('print_paper')),
                      print_exposure=float(sim.get('print_exposure') or 0.0),
                      auto_exposure=False,
                      wb=('custom' if use.get('temperature') is not None
                          and use.get('tint') is not None else 'as_shot'),
                      wb_temperature=float(use.get('temperature') or 0.0),
                      wb_tint=float(use.get('tint') or 1.0),
                      wb_calibrated=False,
                      amount=float((m.get('couplers') or {}).get('amount') or 0.0))
        print('  --reuse ⇒ 复用已渲的 ①（只重测）')
        try:
            recs = measure_engine_dir(b, log=print, expect=expect)
        except Exception as ex:                            # noqa: BLE001
            print('  复用被拒：%s' % str(ex)[:160])
            print('  ⇒ 退回重渲（介质/WB/amount 变了，旧 ① 不能复用）')
            recs = {}
        if not recs:
            recs = run_engine(b, card_mc, limit=a.render_limit, log=print,
                              targets=flat, table=table,
                              stock=getattr(a, 'stock', '') or '',
                              paper=getattr(a, 'paper', '') or '')
    else:
        recs = run_engine(b, card_mc, limit=a.render_limit, log=print,
                          targets=flat, table=table,
                          stock=getattr(a, 'stock', '') or '',
                          paper=getattr(a, 'paper', '') or '')
    if not recs:
        print('整批无一有效 ⇒ 停')
        return

    print('\n[3/4] LR-A 批级护栏 + LR-B 计划（EV 0）')
    card = raw_card(card_mc.name)
    if card is None:
        print('找不到卡的原样数据：%s' % card_mc.name)
        return
    guard = build_guard(recs, card)
    for line in guard.get('ledger') or []:
        print('  ' + line)

    peers = targets
    if peers:
        got = sum(1 for v in peers.values() if (v.get('peer_q') or {}).get('p50') is not None)
        print('  peer target：%d/%d 张' % (got, len(peers)))
    else:
        print('  ! 没有 peer target ⇒ 回退 hero/卡目标（记账）')
    hero_target = _hero_target(b.hero)
    # 引擎状态从 ① 的 exposure.json 读（交接契约的一部分）
    status_map = {}
    for st in recs:
        f = os.path.join(b.sub('engine'), '%s_exposure.json' % st)
        if os.path.isfile(f):
            try:
                status_map[st] = (json.load(open(f, encoding='utf-8')) or {}).get('status') or ''
            except Exception:                              # noqa: BLE001
                pass
    plans = plan_batch(recs, card, peer_targets=peers,
                       hero_target=hero_target, status_map=status_map, log=print)
    print(lr_summary(plans, card['name'], pe=card.get('pe')))

    # 模拟 ③（只套曲线）：把**实测**的暗差写回计划，不拿锚点意图当结果
    peer_q_map = {st: ((peers or {}).get(st) or {}).get('peer_q') for st in plans}
    try:
        from . import sim3 as _sim3
        off = os.environ.get('SV_SIM3', '1') != '0'
        if off:
            _m = _sim3.measure_plans(plans, b.sub('engine'),
                                     os.path.join(b.out_dir, 'final_sim'),
                                     peer_q=peer_q_map, log=print)
            for _st, _pl in plans.items():
                _r = _m.get(_st) or {}
                if _r.get('gap_after') is not None:
                    _pl.shadow_gap_after = round(float(_r['gap_after']), 1)
            _gs = [x['gap_after'] for x in _m.values() if x.get('gap_after') is not None]
            if _gs:
                print('  ③ 模拟暗差：中位 %+.1f ｜ 0 附近的 %d/%d 张'
                      % (sorted(_gs)[len(_gs) // 2], sum(1 for g in _gs if abs(g) <= 5), len(_gs)))
    except Exception as _e:                                # noqa: BLE001
        print(' ! 模拟 ③ 失败：%s' % _e)

    # 计划落盘：sim3 / 复核 / 以后对 XMP 都对得上
    try:
        _pj = {}
        for _st, _pl in plans.items():
            _pt = (peers or {}).get(_st) or {}
            _pj[_st] = dict(curve=getattr(_pl, 'curve', None),
                            target=getattr(_pl, 'target', None),
                            status=getattr(_pl, 'status', None),
                            target_source=getattr(_pl, 'target_source', None),
                            face_mid_target=getattr(_pl, 'face_mid_target', None),
                            shadow_gap_before=getattr(_pl, 'shadow_gap_before', None),
                            shadow_gap_after=getattr(_pl, 'shadow_gap_after', None),
                            peer_q=_pt.get('peer_q'),
                            target_from=_pt.get('target_from'))
        with open(os.path.join(b.out_dir, 'plans.json'), 'w', encoding='utf-8') as _f:
            json.dump(_pj, _f, ensure_ascii=False, indent=1)
    except Exception as _e:                                # noqa: BLE001
        print(' ! 计划落盘失败：%s' % _e)

    if a.export:
        print('\n[4/4] 出片：LR 出 ③ → 量 → 目检页（唯一驱动 LR 的一步）')
        try:
            got = lr_run(plans, b.sub('engine'), b.out_dir, card=card['name'],
                         pe=card.get('pe'), log=print)
            print(' ③ 成片 %d 张' % len(got))
            recs3 = {}
            for st, p in got.items():
                r = measure_tif(p)
                r['file'] = os.path.basename(p)
                recs3[st] = r
            out = build_review(b, recs, plans, recs3, log=print)
            print(' 目检：%s' % out['html'])
        except Exception as e:                             # noqa: BLE001
            print(' ! LR 出片失败：%s' % str(e)[:200])
            print('   （算值与 XMP 作业单不受影响；LR 没启动只会挡"出片"）')
    else:
        print('\n（没给 --export ⇒ 只算计划、不驱动 LR；给 --export 才出 ③）')

    print('\n产物：%s（engine/ 与 xmp/）' % b.out_dir)


# ---------------------------------------------------------------- 只写作业单 / 目检

def _load_recs(path: str) -> dict:
    doc = json.load(open(path, encoding='utf-8'))
    rows = doc.get('per_image') if isinstance(doc, dict) else doc
    if isinstance(rows, list):
        out = {}
        for r in rows:
            st = os.path.basename(str(r.get('file') or ''))
            for cut in ('_engine.tif', '_final.tif', '_xmp.tif', '_sim.png',
                        '.tif', '.tiff', '.jpg', '.jpeg', '.png'):
                if st.lower().endswith(cut):
                    st = st[:-len(cut)]
                    break
            if '_' in st:
                st = st.split('_')[0]
            out[st] = r
        return out
    return dict(rows or {})


def cmd_xmp(a):
    """① 测量 + 卡 → LRPlan → XMP 作业单（不驱动 LR）。"""
    recs = _load_recs(a.recs)
    card = raw_card(a.card)
    if card is None:
        print('没有这张卡：%s' % a.card)
        return
    peers = {}
    if a.peers:
        peers = json.load(open(a.peers, encoding='utf-8'))
    hero_target = _hero_target(getattr(a, 'hero', ''))
    plans = plan_batch(recs, card, peer_targets=peers,
                       hero_target=hero_target, log=print)
    print(lr_summary(plans, card['name'], pe=card.get('pe')))
    mf = write_jobs(plans, a.out, manifest_extra=dict(card=card['name'], step='xmp',
                                                      print_exposure=card.get('pe')))
    print()
    print(xmp_summary(mf))
    print('输出：%s' % a.out)


def cmd_simcurve(a):
    """模拟 ③（不驱动 LR）：曲线套到 ① 上 → 量 → 跟 peer 比。"""
    from . import sim3
    res = sim3.run(a.run_dir, log=print)
    print(sim3.table(res))


def cmd_review_sim(a):
    """①/③(模拟)/peer 三图并排的目检页。"""
    import glob as _glob
    from . import sim3
    from .review import make_review
    from .matchcard import card_samples
    run = a.run_dir
    eng = os.path.join(run, 'engine')
    recs1 = {}
    for f in _glob.glob(os.path.join(eng, '*_rec1.json')):
        st = os.path.basename(f)[:-len('_rec1.json')]
        recs1[st] = json.load(open(f, encoding='utf-8'))
    plans = json.load(open(os.path.join(run, 'plans.json'), encoding='utf-8'))
    doc = json.load(open(os.path.join(run, 'peer_targets.json'), encoding='utf-8'))
    per = doc.get('per_image') or {}
    peer_q = {st: (per.get(st) or {}).get('peer_q') for st in plans}
    m3 = sim3.measure_plans(plans, eng, os.path.join(run, 'final_sim'),
                            peer_q=peer_q, log=print)
    recs3 = {st: dict(img=dict(p5=v['p5'], p50=v['p50'], p95=v['p95']))
             for st, v in m3.items()}
    samples = {os.path.basename(p): p for p in card_samples(doc.get('card') or '')}
    peer_src = {}
    for st in plans:
        ids = (per.get(st) or {}).get('peer_ids') or []
        if ids and samples.get(ids[0]):
            peer_src[st] = samples[ids[0]]
    sub = '① 引擎 / ③ 模拟（只套曲线）/ peer 靶 —— 数字只用来定位，不判过不过'
    out = make_review(recs1, recs3, plans, run, tif_1_dir=eng,
                      tif_3_dir=os.path.join(run, 'final_sim'),
                      peer_src=peer_src, title='①/③/peer 目检', sub=sub, log=print)
    print('\n目检页：%s（%d 张）' % (out['html'], out['n']))


def cmd_review(a):
    """①/③ 测量 + manifest → 目检页（**不判过/不过**）。"""
    r1 = _load_recs(a.recs1)
    r3 = _load_recs(a.recs3)
    plans = _plans_from_manifest(a.manifest) if a.manifest else \
        {st: types.SimpleNamespace() for st in r3}
    um = json.load(open(a.urls, encoding='utf-8')) if a.urls else {}
    out = make_review(r1, r3, plans, a.out, tif_1_dir=a.tif1, tif_3_dir=a.tif3,
                      title=a.title, url_map=um, hero=a.hero, log=print)
    print('\n目检页：%s（%d 张）' % (out['html'], out['n']))
    print('机器只出片 + 测量；过不过由你目检。')


def main(argv=None):
    ap = argparse.ArgumentParser(prog='design')
    sub = ap.add_subparsers(dest='cmd', required=True)

    sub.add_parser('stocks').set_defaults(fn=cmd_stocks)
    sub.add_parser('cards').set_defaults(fn=cmd_cards)

    p = sub.add_parser('match-jpg'); p.set_defaults(fn=cmd_match_jpg)
    p.add_argument('--raf', required=True, help='RAW 目录')
    p.add_argument('--out', required=True, help='输出目录（写 jpg/、match/）')
    p.add_argument('--content', default='', help='batch_content JSON（人给场景/服装）')
    p.add_argument('--top', type=int, default=10)
    p.add_argument('--limit', type=int, default=0, help='只跑前几张（试跑用）')

    p = sub.add_parser('page'); p.set_defaults(fn=cmd_page)
    p.add_argument('--out', required=True)
    p.add_argument('--title', default='')

    p = sub.add_parser('run'); p.set_defaults(fn=cmd_run)
    p.add_argument('--raf', required=True, help='RAW 目录')
    p.add_argument('--out', required=True, help='输出目录')
    p.add_argument('--limit', type=int, default=0, help='本批最多几张 RAW')
    p.add_argument('--render-limit', type=int, default=0, help='只渲前几张（试跑用）')
    p.add_argument('--card', default='', help='人拍板的卡名（不给则只做内容匹配）')
    p.add_argument('--content', default='', help='batch_content JSON（匹配用）')
    p.add_argument('--hero', default='', help='hero 样片（目检参考 + peer 不足兜底）')
    p.add_argument('--stock', default='', help='临时覆盖卷（跑介质对比用）')
    p.add_argument('--paper', default='', help='临时覆盖纸（跑介质对比用）')
    p.add_argument('--export', action='store_true', help='用 LR 出 ③（要 LR 在跑）')
    p.add_argument('--reuse', action='store_true', help='复用已渲好的 ①（只重测）')

    p = sub.add_parser('xmp'); p.set_defaults(fn=cmd_xmp)
    p.add_argument('recs', help='① 的测量 JSON（measure_all 的 per_image）')
    p.add_argument('--card', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--peers', default='', help='peer.build 的输出 JSON（可选）')
    p.add_argument('--hero', default='', help='hero 样片（peer 不足兜底）')

    p = sub.add_parser('review'); p.set_defaults(fn=cmd_review)
    p.add_argument('recs1', help='① 的测量 JSON')
    p.add_argument('recs3', help='③ 的测量 JSON')
    p.add_argument('--out', required=True)
    p.add_argument('--tif1', default='', help='① 的 tif 目录')
    p.add_argument('--tif3', default='', help='③ 的 tif 目录')
    p.add_argument('--manifest', default='', help='XMP 作业单 manifest.json')
    p.add_argument('--urls', default='', help='图片直链 json（手机端）')
    p.add_argument('--hero', default='', help='hero 样片')
    p.add_argument('--title', default='成片目检')

    p = sub.add_parser('review-sim'); p.set_defaults(fn=cmd_review_sim)
    p.add_argument('run_dir')

    p = sub.add_parser('simcurve'); p.set_defaults(fn=cmd_simcurve)
    p.add_argument('run_dir', help='run 的输出目录（要含 plans.json 与 engine/）')

    a = ap.parse_args(argv)
    a.fn(a)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
