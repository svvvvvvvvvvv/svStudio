# -*- coding: utf-8 -*-
r"""v9 第 1 层：脸锚曝光 + 固定 LookProfile 曲线（不搜 g、不解通道）。

流程：
  ① 量底片（EV=0）→ 脸颊 L_skin（左右脸蛋 ∩ 肤色）
  ② ScenePolicy：从脸反推 EV；若 EV 把贴白/压黑打超限，逐档退回
  ③ 用固定 LookProfile 曲线（P 随脸，形状常量）写一次
  ④ 量终态，记账

用法：
  python opt/layer1_v9.py --profile kurai --batch <底片目录> --only DSCF2497
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths
paths.ensure_import_path()
import profile_io                                   # noqa: E402
import scene                                        # noqa: E402
import look                                         # noqa: E402
import curve_lib                                    # noqa: E402
from _probe_clip import measure                     # noqa: E402
from bridge_client import run                       # noqa: E402
import lr_boot                                      # noqa: E402

XS = curve_lib.XS


def expo(outdir, edge=2048):
    last, r = None, ''
    for _ in (1, 2):
        od = os.path.join(outdir, 'e%d' % time.time_ns())
        os.makedirs(od, exist_ok=True)
        r = run('EXPORTTIF %s %d' % (od, edge), timeout=180)
        time.sleep(0.3)
        fs = [os.path.join(od, f) for f in os.listdir(od) if f.lower().endswith('.tif')]
        last = fs[0] if fs else None
    return last, r


def spec_of(ys):
    body = ','.join('%d,%d' % (x, y) for x, y in zip(XS, ys))
    # 只写复合曲线。10-24 研究：同时写 Red/Green/Blue 会让 LR 把同一条曲线
    # 再叠一次（有效 g≈g²，J 的 0.85 实际 0.72，p2 3.55、1289 黑 21.66）。
    return 'ToneCurvePV2012:%s' % body


def snap(p):
    d = measure(p) or {}
    return d


def write_ev(src, ev):
    run('GLOBAL %s Exposure2012=%.4f' % (src, ev), timeout=60)


def write_basic(src, b):
    keys = ('Exposure2012', 'Contrast2012', 'Highlights2012',
            'Shadows2012', 'Whites2012', 'Blacks2012')
    spec = ','.join('%s=%.3f' % (k, float(b.get(k, 0.0))) for k in keys)
    run('GLOBAL %s %s' % (src, spec), timeout=60)


def solve_one(src, prof, edge, out_root, basic_row=None):
    stem = os.path.basename(src).replace('_svFilm_full.tif', '')
    tag = os.path.join(out_root, 'layer1_v9', stem)
    os.makedirs(tag, exist_ok=True)

    run('SELECT ' + src, timeout=60)
    run('RESET ' + src, timeout=60)
    m0 = snap(src)
    miss = scene.missing_anchor(m0)
    if miss:
        return dict(stem=stem, src=src, action='skip', why=miss), None

    t = profile_io.tone(prof)
    sc = profile_io.scene(prof)
    gd = profile_io.guards(prof)
    wht, blk = float(gd['white_p90']), float(gd['black_p90'])

    if basic_row:
        b = dict(basic_row.get('settings') or {})
        write_basic(src, b)
        fev, _ = expo(os.path.join(tag, 'basic'), edge)
        mev = snap(fev) if fev else {}
        if not mev.get('face'):
            return dict(stem=stem, src=src, action='skip', why='basic 后量不到脸'), None
        ev = float(b.get('Exposure2012', 0.0))
        rec_basic = b
    else:
        ev0 = scene.face_ev(m0['L_skin'], sc['face_zone'], float(t['l_per_ev']))
        picked = None
        for s in (1.0, 0.75, 0.5, 0.25, 0.0):
            ev = ev0 * s
            write_ev(src, ev)
            f, _ = expo(os.path.join(tag, 'ev%.2f' % s), edge)
            if not f:
                continue
            m = snap(f)
            if m.get('face') and scene.within(m, wht, blk):
                picked = (ev, m, f)
                break
        if picked is None:
            write_ev(src, 0.0)
            f, _ = expo(os.path.join(tag, 'ev0'), edge)
            picked = (0.0, snap(f), f)
        ev, mev, fev = picked
        rec_basic = None

    P = curve_lib.L_to_code(mev['L_skin'])
    # 压黑贴边时**加大 toe**（提暗），不是减小——toe 是暗部的补偿，不是黑点的来源。
    b0 = float(mev.get('black', 0.0))
    lim = max(1e-6, float(blk))
    toe_scale = 1.0 + max(0.0, min(1.0, b0 / lim))
    lk = profile_io.look(prof)
    # 逐图 look 覆盖（直方图驱动：同一族，参数按图给）
    per = ((prof.get('look') or {}).get('per_image') or {}).get(stem)
    if per:
        lk = {**lk, **per}
    ys = look.curve(P, lk, toe_scale=toe_scale)

    settings = dict(rec_basic or {})
    settings.setdefault('Exposure2012', ev)
    for k in ('Contrast2012', 'Highlights2012', 'Shadows2012',
              'Whites2012', 'Blacks2012'):
        settings.setdefault(k, 0.0)
    spec = ','.join('%s=%.3f' % (k, float(settings[k])) for k in
                    ('Exposure2012', 'Contrast2012', 'Highlights2012',
                     'Shadows2012', 'Whites2012', 'Blacks2012'))
    ls = look.global_spec(lk)
    if ls:
        spec += ',' + ls
    run('GLOBAL %s %s' % (src, spec), timeout=60)
    run('CURVES %s %s' % (src, spec_of(ys)), timeout=120)
    ffin, _ = expo(os.path.join(tag, 'final'), 2048)
    mfin = snap(ffin) if ffin else {}
    rec = dict(stem=stem, src=src, ev=ev, P=P, base=m0, after_ev=mev,
               basic=rec_basic, final=mfin, curve=ys, final_tif=ffin,
               action='已执行' if abs(ev) > 0.01 or ys != list(XS) else '零动作')
    if mfin and not scene.within(mfin, wht, blk):
        rec['guard'] = 'curve 后脸过曝，归上游/需人工'
    return rec, ffin


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile', default='kurai')
    ap.add_argument('--batch', default=str(paths.BATCH_ROOT / '_b2048_1022'))
    ap.add_argument('--only', default='')
    ap.add_argument('--out-root', default=str(paths.WORK))
    ap.add_argument('--edge', type=int, default=1024)
    ap.add_argument('--no-basic', action='store_true', help='不读 basic/basic.json')
    a = ap.parse_args()
    prof = profile_io.load(a.profile)
    if prof.get('status') != 'calibrated':
        print('⚠ 预设 %s 状态=%s：结果只作演示' % (prof.get('id'), prof.get('status')))

    basic_rows = {}
    if not a.no_basic:
        bp = os.path.join(a.out_root, 'basic', 'basic.json')
        if os.path.isfile(bp):
            basic_rows = {r['stem']: r for r in
                          json.load(open(bp, encoding='utf-8'))['rows']}
            print('Basic 基线：%s（%d 张）' % (bp, len(basic_rows)))

    srcs = sorted(p for p in os.listdir(a.batch)
                  if p.endswith('_svFilm_full.tif') and '_c0' not in p and '_c1' not in p)
    srcs = [os.path.join(a.batch, p) for p in srcs]
    if a.only:
        want = set(x.strip() for x in a.only.split(','))
        srcs = [p for p in srcs if os.path.basename(p).split('_')[0] in want]
    if not srcs:
        print('没有底片：%s' % a.batch); return

    if lr_boot.lr_pids():
        lr_boot.kill_lr(); time.sleep(3)
    print('日志清理: %s ｜ 锁: %s' % (lr_boot.clear_console_log(), lr_boot.clear_stale_lock()))
    lr_boot.launch()
    ok, msg = lr_boot.wait_bridge(150)
    print('桥：%s %s\n' % ('就绪' if ok else '未就绪', msg))
    if not ok:
        return

    recs = []
    for src in srcs:
        rec_stem = os.path.basename(src).replace('_svFilm_full.tif', '')
        rec, _ = solve_one(src, prof, a.edge, a.out_root, basic_rows.get(rec_stem))
        recs.append(rec)
        f = rec.get('final') or {}
        print('%-10s EV%+.2f  颊 %.1f→%.1f  颊光比 %.1f→%.1f  贴白 %.2f  压黑 %.2f  %s'
              % (rec['stem'], rec.get('ev', 0), (rec.get('base') or {}).get('L_skin', 0),
                 f.get('L_skin', 0), (rec.get('base') or {}).get('skin_span', 0),
                 f.get('skin_span', 0),
                 f.get('white', 0), f.get('black', 0), rec.get('guard', '')))
    dst = os.path.join(a.out_root, 'layer1_v9', 'layer1_v9.json')
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    json.dump(dict(profile=prof.get('id'), profile_version=prof.get('version'),
                   rows=recs), open(dst, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1, default=float)
    print('\n-> %s' % dst)


if __name__ == '__main__':
    main()
