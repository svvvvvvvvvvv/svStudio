# -*- coding: utf-8 -*-
r"""把第 2 层算出来的**通道曲线**写进 LR，复量五段 —— 第 2 层闭环验证。

为什么必须单独跑一遍：`layer2_curve.py` 给的是**预测**（"半量之后应该到哪"）。
预测不是证据。判参数进没进画面，只能看导出图逐像素（插件读回、catalog 值都撒过谎）。

状态 = 第 1 层定稿（曝光 + 高光 + 主曲线）**再叠**第 2 层的 R/G/B 曲线。
主曲线按 layer1.json 里的 (pivot, g) 原样重建；R/G/B 用 layer2_curve.json。

掩膜：`mask_from` = 第 1 层定稿那张图 ⇒ 前后读的是**同一批近中性像素**。

用法：python opt/apply_layer2.py
产物：<OUT>/<stem>/e<ns>/*.tif + layer2_apply.json + 屏显
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time

import numpy as np

import _boot  # noqa: E402
import paths
paths.ensure_import_path()

import band_read as BR                     # noqa: E402
import lr_measure as M                     # noqa: E402
from bridge_client import run               # noqa: E402
import lr_boot                             # noqa: E402

L1 = str(paths.WORK / 'layer1')
L2 = str(paths.WORK / 'layer2')
XS = list(range(0, 256, 16))
IDENT = [(0, 0), (255, 255)]        # 恒等：写进 LR 后画面一格不动（_probe_curvefmt 已验）


def curve(P, g):
    out = []
    for x in XS:
        if x >= P:
            t = (x - P) / (255.0 - P) if 255.0 - P > 1e-6 else 0.0
            y = P + (255.0 - P) * (t ** g)
        else:
            t = x / P if P > 1e-6 else 0.0
            y = P * (t ** (1.0 / g))
        out.append(int(round(min(255.0, max(0.0, y)))))
    return out


def spec(main, R, G, B):
    """四条表一次写完（分两次会被"整表替换+旧表读回"抹掉）。
    曲线一律是 [(x, y), ...]；主曲线那 16 个值先转成点对。"""
    def norm(c):
        if c and isinstance(c[0], (tuple, list)):
            return [(int(x), int(y)) for x, y in c]
        return [(x, int(y)) for x, y in zip(XS, c)]

    def s(c):
        return ','.join('%d,%d' % (x, y) for x, y in norm(c))
    return ('ToneCurvePV2012:%s;ToneCurvePV2012Red:%s;'
            'ToneCurvePV2012Green:%s;ToneCurvePV2012Blue:%s'
            % (s(main), s(R), s(G), s(B)))


def expo(outdir, edge=2048):
    last = None
    for _ in (1, 2):
        od = os.path.join(outdir, 'e%d' % time.time_ns())
        os.makedirs(od, exist_ok=True)
        r = run('EXPORTTIF %s %d' % (od, edge), timeout=180)
        time.sleep(0.4)
        fs = [os.path.join(od, f) for f in os.listdir(od) if f.lower().endswith('.tif')]
        last = fs[0] if fs else None
    return last, r


def final_tif(stem, row):
    """第 1 层定稿那张图的路径（= 第 2 层的底片）。"""
    sd = os.path.join(L1, stem)
    g, ev, hl = row.get('g') or 1.0, row.get('ev') or 0.0, row.get('hl') or 0
    sub = ('g%.2f' % g) if g and abs(g - 1.0) > 1e-6 else ('pos%+.2f_h%d' % (ev, abs(hl)))

    def newest(d):
        if not os.path.isdir(d):
            return None
        for s in sorted((x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))), reverse=True):
            fs = [f for f in os.listdir(os.path.join(d, s)) if f.lower().endswith('.tif')]
            if fs:
                return os.path.join(d, s, fs[0])
        return None
    return newest(os.path.join(sd, sub))


def bands(path, mask_from):
    r = BR.read(path, mask_from=mask_from)
    return {b['band']: b for b in r['bands']}, r


def main():
    plan2 = json.load(open(os.path.join(L2, 'layer2_curve.json'), encoding='utf-8'))
    rows1 = {r['stem']: r for r in
             json.load(open(os.path.join(L1, 'layer1.json'), encoding='utf-8'))['rows']}
    cv = plan2['curves']
    mas = BR.master()

    jobs = []
    for path in plan2['files']:
        stem = os.path.basename(path).replace('_svFilm_full.tif', '')
        r1 = rows1.get(stem)
        if not r1:
            continue
        # ★ 要动的是**目录里那张原底片**。第 1 层定稿是导出产物，不归 LR，SELECT 不到
        #   （第一次跑就是这么错的：EXPORTED_TIF 0，一张没导出来）。
        src = r1.get('src')
        l1f = final_tif(stem, r1)
        if not src or not os.path.exists(src):
            print('  %s 找不到原底片，跳过' % stem); continue
        # l1f（layer1 目录里的定稿 tif）**不再当基准**，只留着便于人工对照；
        # 缺了也不拦 —— 基准改成"LR 当前画面导出"，跟这张文件无关。
        jobs.append((stem, src, l1f, r1))
    if not jobs:
        print('没有可跑的任务'); return

    if lr_boot.lr_pids():
        lr_boot.kill_lr(); time.sleep(3)
    print('日志清理: %s ｜ 锁: %s' % (lr_boot.clear_console_log(), lr_boot.clear_stale_lock()))
    lr_boot.launch()
    ok, msg = lr_boot.wait_bridge(150)
    print('桥：%s %s\n' % ('就绪' if ok else '未就绪', msg))
    if not ok:
        return

    out = []
    for stem, src, l1f, r1 in jobs:
        ev, hl = r1.get('ev') or 0.0, r1.get('hl') or 0
        g, P = r1.get('g') or 1.0, r1.get('P') or 128.0
        mainy = curve(P, g) if (g and abs(g - 1.0) > 1e-6) else list(XS)
        run('SELECT ' + src, timeout=60)
        run('RESET ' + src, timeout=60)
        if abs(ev) > 0.01:
            run('GLOBAL %s Exposure2012=%.4f' % (src, ev), timeout=60)
        if abs(hl) > 0.5:
            run('GLOBAL %s Highlights2012=%d' % (src, hl), timeout=60)
        # ★★ "前"必须是 LR 里**当前真实画面**的导出，不是 layer1 目录里那张旧 tif。
        #   10-23 20:2x 实测：两者差 21 个 L*（55.2 vs 76.2）—— 拿旧 tif 当基准，
        #   "改前→改后"读的就不是同一件事。所以先只写"主曲线 + 三条恒等"导一次当基准。
        run('CURVES %s %s' % (src, spec(mainy, IDENT, IDENT, IDENT)), timeout=120)
        bf, rb = expo(os.path.join(L2, stem + '_before'))
        if not bf:
            print('  %s 基准导出失败 %s' % (stem, rb)); continue
        # 后 = 在基准上再叠 R/G/B
        w = run('CURVES %s %s' % (src, spec(mainy, cv['R'], cv['G'], cv['B'])), timeout=120)
        f, r = expo(os.path.join(L2, stem + '_after'))
        if not f:
            print('  %s 导出失败 %s' % (stem, r)); continue
        b0, _ = bands(bf, bf)       # 前 = LR 当前画面；掩膜建在它自己身上
        b1, _ = bands(f, bf)        # 后 = 叠加通道曲线；掩膜仍是同一批像素
        out.append(dict(stem=stem, src=src, before_tif=bf, after=f, before=b0, got=b1))
        print('%s  %s' % (stem, w if 'ok' in (w or '') else (w or '')[:80]))

    print('\n%-8s %5s %8s %8s %8s %8s %6s' %
          ('段', 'n', 'a*前→后', 'b*前→后', '靶a', '靶b', '进带?'))
    for rec in out:
        print('-- %s' % rec['stem'])
        for b in plan2['rows']:
            nm = b['band']
            x, y = rec['before'].get(nm), rec['got'].get(nm)
            if not x or not y or 'na' not in y:
                print('%-8s  中性像素不足' % nm); continue
            m = mas[nm]
            ain = m['a_p25'] <= y['na'] <= m['a_p75']
            bin_ = m['b_p25'] <= y['nb'] <= m['b_p75']
            print('%-8s %5d %+5.1f→%+5.1f %+5.1f→%+5.1f %+6.1f %+6.1f  %s%s'
                  % (nm, y['n_neutral'], x['na'], y['na'], x['nb'], y['nb'],
                     m['a_p50'], m['b_p50'],
                     'a✓' if ain else 'a✗', 'b✓' if bin_ else 'b✗'))

    # 整组汇总：改完之后的组中位是否进带
    print('\n== 整组（改后 5 张逐段取中位）')
    print('%-8s %6s %6s %6s %6s %s' % ('段', 'a*', 'b*', '靶a', '靶b', '进带'))
    summ = []
    for b in plan2['rows']:
        nm = b['band']
        vals = [r['got'][nm] for r in out if nm in r['got'] and 'na' in r['got'][nm]]
        if not vals:
            continue
        a = float(np.median([v['na'] for v in vals]))
        bb = float(np.median([v['nb'] for v in vals]))
        m = mas[nm]
        ain = m['a_p25'] <= a <= m['a_p75']
        bin_ = m['b_p25'] <= bb <= m['b_p75']
        summ.append(dict(band=nm, a=a, b=bb, a_in=ain, b_in=bin_))
        print('%-8s %+6.1f %+6.1f %+6.1f %+6.1f %s'
              % (nm, a, bb, m['a_p50'], m['b_p50'],
                 ('a✓' if ain else 'a✗') + ' ' + ('b✓' if bin_ else 'b✗')))

    json.dump(dict(curves=cv, rows=[{k: v for k, v in r.items() if k in ('stem', 'src', 'after')}
                                    for r in out], summary=summ),
              open(os.path.join(L2, 'layer2_apply.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1, default=float)
    print('\n-> %s' % os.path.join(L2, 'layer2_apply.json'))


if __name__ == '__main__':
    main()
