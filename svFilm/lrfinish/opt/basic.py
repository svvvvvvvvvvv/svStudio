# -*- coding: utf-8 -*-
r"""Basic 影调层：先把 LR 的六个 Basic 旋钮解出来，再交给曲线做残差。

v8 的顺序：
  ① Exposure      → 脸落 VI 区 / 整体落点
  ② Highlights    → 压贴白 / 收脸亮端
  ③ Shadows       → 压黑 / 提暗部
  ④ Whites/Blacks → 钉两端分位
  ⑤ Contrast      → 补剩余光比
之后才轮到点曲线（只补 Basic 没做到的形状）。

这里用**每张两次探针的割线**解每个旋钮（不是固定增益）：
  写一档 → 导出 → 量 → 得到 ∂metric/∂knob → 反解目标 → 再写。
优点：不依赖先前标定，换机器/换介质也能用；缺点：每张多几次导出。
以后可用 `basic_gain.json` 替代探针（同一套接口）。

用法：
  python opt/basic.py --profile kurai --batch <底片目录> --only DSCF2497
  python opt/basic.py --profile kurai --batch <dir> --out-root <根>
产物：<out>/basic/<stem>/ + basic.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths
paths.ensure_import_path()
import profile_io                                   # noqa: E402
import scene                                        # noqa: E402

from _probe_clip import measure                      # noqa: E402
from bridge_client import run                        # noqa: E402
import lr_boot                                       # noqa: E402

KEYS = ('Exposure2012', 'Contrast2012', 'Highlights2012',
        'Shadows2012', 'Whites2012', 'Blacks2012')
ZERO = {k: 0.0 for k in KEYS}
BOUNDS = {
    'Exposure2012': (-2.0, 2.0),
    'Contrast2012': (-100.0, 100.0),
    'Highlights2012': (-100.0, 100.0),
    'Shadows2012': (-100.0, 100.0),
    'Whites2012': (-100.0, 100.0),
    'Blacks2012': (-100.0, 100.0),
}


def clip_key(k, v):
    lo, hi = BOUNDS[k]
    return float(np.clip(v, lo, hi))


def write_basic(src, vals):
    spec = ','.join('%s=%.3f' % (k, vals[k]) for k in KEYS)
    return run('GLOBAL %s %s' % (src, spec), timeout=60)


def expo(outdir, edge=1024):
    last, r = None, ''
    for _ in (1, 2):
        od = os.path.join(outdir, 'e%d' % time.time_ns())
        os.makedirs(od, exist_ok=True)
        r = run('EXPORTTIF %s %d' % (od, edge), timeout=180)
        time.sleep(0.3)
        fs = [os.path.join(od, f) for f in os.listdir(od) if f.lower().endswith('.tif')]
        last = fs[0] if fs else None
    return last, r


def snap(path):
    r = measure(path)
    return r or {}


def _state(src, vals, tag, edge):
    write_basic(src, vals)
    f, r = expo(os.path.join(tag), edge)
    if not f:
        return None, None, r
    return vals, snap(f), f


def _secant(src, vals, m, key, metric, target, tag, edge, step, lo, hi, log):
    """割线解一个旋钮：写一档 → 量增益 → 反解 → 再写。"""
    cur = float(m.get(metric, float('nan')))
    if not np.isfinite(cur):
        return vals, m, '缺 %s' % metric
    if abs(cur - target) <= 0.02:
        return vals, m, '%s 已在目标' % key
    v = dict(vals)
    v[key] = clip_key(key, v[key] + step)
    if abs(v[key] - vals[key]) < 1e-6:
        return vals, m, '%s 到边界' % key
    _, m2, _ = _state(src, v, os.path.join(tag, '%s_probe' % key), edge)
    if not m2:
        return vals, m, '%s 探针导出失败' % key
    g = (float(m2.get(metric, float('nan'))) - cur) / (v[key] - vals[key])
    if not np.isfinite(g) or abs(g) < 1e-5:
        return vals, m, '%s 增益≈0（%.2f）' % (key, g if np.isfinite(g) else float('nan'))
    want = vals[key] + (target - cur) / g
    want = clip_key(key, want)
    v2 = dict(vals)
    v2[key] = want
    _, m3, f3 = _state(src, v2, os.path.join(tag, '%s_solve' % key), edge)
    if not m3:
        return vals, m, '%s 反解导出失败' % key
    log.append(dict(key=key, metric=metric, before=cur, after=float(m3.get(metric, np.nan)),
                    gain=float(g), value=float(want)))
    return v2, m3, '%s %.1f→%.1f（%.2f/格）' % (key, cur, m3.get(metric, float('nan')), g)


def solve_one(src, prof, edge, out_root):
    stem = os.path.basename(src).replace('_svFilm_full.tif', '')
    tag = os.path.join(out_root, 'basic', stem)
    os.makedirs(tag, exist_ok=True)
    run('SELECT ' + src, timeout=60)
    run('RESET ' + src, timeout=60)

    t = profile_io.tone(prof)
    face_lo, face_hi = float(t['face_lo']), float(t['face_hi'])
    l_per_ev = float(t['l_per_ev'])
    # ★ 10-24 用户拍板：背景的爆/暗是风格，不再当缺陷；只保护脸。
    #   white_p90/black_p90 保留在 profile 里当"风格描述子"，不再驱动动作。
    # ★ 脸的口径 = 左右脸蛋两圈 ∩ 肤色门（`L_skin`），不是整张脸框（p50）。

    vals = dict(ZERO)
    _, m0, f0 = _state(src, vals, os.path.join(tag, 'base'), edge)
    if not m0 or not m0.get('face'):
        return dict(stem=stem, src=src, action='skip', why='量不到脸'), None
    if scene.face_blown(m0):
        return dict(stem=stem, src=src, action='skip', why='face_blown',
                    base=m0), None
    rec = dict(stem=stem, src=src, base=m0, settings={}, actions=[])
    log = rec['actions']

    # ① Exposure：脸颊落带；一旦把脸打爆，就退到不爆的那一档
    if m0['L_skin'] > face_hi:
        ev0 = (face_hi - 0.5 - m0['L_skin']) / l_per_ev
    elif m0['L_skin'] < face_lo:
        ev0 = (face_lo + 2.0 - m0['L_skin']) / l_per_ev
    else:
        ev0 = 0.0
    picked = None
    for s in (1.0, 0.75, 0.5, 0.25, 0.0):
        v = dict(vals)
        v['Exposure2012'] = clip_key('Exposure2012', ev0 * s)
        _, mm, ff = _state(src, v, os.path.join(tag, 'ev%.2f' % s), edge)
        if mm and not scene.face_blown(mm):
            picked = (v, mm, ff)
            break
    if picked is None:
        vals['Exposure2012'] = 0.0
        _, m, f = _state(src, vals, os.path.join(tag, 'ev0'), edge)
        rec['upstream'] = '抬曝光会打爆脸；EV 退回 0'
    else:
        vals, m, f = picked
    if not m:
        return dict(stem=stem, src=src, action='fail', why='EV 导出失败'), None
    rec['after_ev'] = m
    log.append(dict(key='Exposure2012', value=vals['Exposure2012'],
                    before=m0['L_skin'], after=m['L_skin'],
                    white=m.get('white'), face_clip95=m.get('clip95')))

    # ② 背景的贴白/压黑是风格（10-24 用户拍板）：不再用全图 white/black
    #    驱动 Highlights/Shadows。脸自己的过曝在入口已经 skip。
    #
    # ③ 不做 Whites/Blacks 的 p99/p10 追端：
    #    10-24 实测（1665/1726）——没有全图护栏后，它们会 +100/−100，
    #    把背景吹到 22.9%、脸压到 51.0。脸的明暗只由 EV + 光比（Contrast）管。
    # ④ 不做 Contrast：脸颊光比（skin_span）是形状指标，全局 Contrast 改不了它；
    #    10-24 实测 +82.6/+100 会把黑影压到 11-18%、颊抬到 75+。脸只由 EV 管。

    # 定稿：2048 一次
    write_basic(src, vals)
    f, r = expo(os.path.join(tag, 'final'), 2048)
    rec.update(settings=vals, final=m, final_tif=f,
               action='已执行' if any(abs(v) > 0.01 for v in vals.values()) else '零动作')
    return rec, f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile', default='kurai')
    ap.add_argument('--batch', default=str(paths.BATCH_ROOT / '_b2048_1022'))
    ap.add_argument('--only', default='')
    ap.add_argument('--out-root', default=str(paths.WORK))
    ap.add_argument('--edge', type=int, default=1024)
    a = ap.parse_args()
    prof = profile_io.load(a.profile)
    if prof.get('status') != 'calibrated':
        print('⚠ 预设 %s 状态=%s：Basic 目标沿用该预设，结果只作演示'
              % (prof.get('id'), prof.get('status')))

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
        rec, _ = solve_one(src, prof, a.edge, a.out_root)
        recs.append(rec)
        s = rec.get('settings') or {}
        print('%-10s %s  %s' % (rec['stem'], rec.get('action'),
                                ' '.join('%s=%.1f' % (k.replace('2012', ''), v)
                                         for k, v in s.items() if abs(v) > 0.01)))
    dst = os.path.join(a.out_root, 'basic', 'basic.json')
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    json.dump(dict(profile=prof.get('id'), profile_version=prof.get('version'),
                   rows=recs), open(dst, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1, default=float)
    print('\n-> %s' % dst)


if __name__ == '__main__':
    main()
