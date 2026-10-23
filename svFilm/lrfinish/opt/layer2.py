# -*- coding: utf-8 -*-
r"""第 2 层求解器（闭环版，**逐张**）：**量基准 → 每张各算 → 写进 LR → 复量一致性**。

★★★ 10-23 18:5x SV 纠偏：第 2 层原来是"整组一条曲线"，被他推翻了 ——
  「求解器…要对每张照片去计算出最好的色调曲线和颜色分级…虽然是同一组照片但是场景不一样
    光线条件构图也不一样…风格当然是需要保证整组一致 因为是发到社交圈」
  实测证明他说的对：同一条曲线砍在五张上，80-100 段红绿极差 **1.4→2.0（更散）**，
  60-80 段 **7.2→7.1（一点没动）**，3024 那张 +6.5 离群 7 个点完全没管。
  ⇒ **靶一致（同一个带），刀逐张（各解各的码值位移）**。

为什么必须闭环（10-23 20:3x 教训）：
  原来分两个脚本：`layer2_curve.py` 从 `layer1` 目录的旧 tif 量五段，
  `apply_layer2.py` 从"LR 当前画面导出"量前后 —— **两者量的不是同一张图**。
  实测同一段 b* 差 1.5（−3.2 vs −4.7）⇒ 算出来的 d 一开始就对错了底片。
  ⇒ 基准只留一份：本脚本自己导，自己量，自己写，自己复量。

状态 = 第 1 层定稿（曝光 + 高光 + 主曲线）**再叠**这张自己的 R/G/B 通道曲线。
  · 曝光键必须 `Exposure2012`（写 `Exposure` 导出像素差 0.0，VERIFIED 也骗人）
  · 四条曲线**一次写完**（分两次会被整表替换抹掉）
  · 通道曲线 **≤20 点**（超了静默截断，见 `_probe_curvefmt.py`）
  · 主曲线 16 点缺 255 是安全的（同一实验已验）
  · **判据（10-23 19:1x SV 纠偏后）**：**逐张落进鹿井的带**（每段 a*/b* 各一格，数进带格数）。
    "五张之间极差收窄"只是**副产品**，降级成分布体检 —— 极差小但一起偏是更糟的。
    SV 原话：「谁说了整组可以一个值？我说的整组可以一致意思是都有鹿井的风格 都是仿鹿井」。

用法：python opt/layer2.py
产物：<OUT>/<stem>/{base,after}/e<ns>/*.tif + layer2.json + 屏显
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _boot  # noqa: E402
import paths
paths.ensure_import_path()
import profile_io                                   # noqa: E402

import band_read as BR                      # noqa: E402
import layer2_curve as LC                   # noqa: E402
import lr_boot                              # noqa: E402
from apply_layer2 import curve, spec, XS, IDENT   # noqa: E402
from bridge_client import run               # noqa: E402

WORK = str(paths.WORK)
L1 = os.path.join(WORK, 'layer1')
OUT = os.path.join(WORK, 'layer2')
ORDER = ('DSCF2532', 'DSCF3024', 'DSCF2891', 'DSCF2497', 'DSCF2572')


def expo(tag, edge=2048):
    """导出两次取第二次；每次写全新空目录（零删除，避开同名模态框与批量删除阈值）。"""
    last = None
    for _ in (1, 2):
        od = os.path.join(OUT, tag, 'e%d' % time.time_ns())
        os.makedirs(od, exist_ok=True)
        r = run('EXPORTTIF %s %d' % (od, edge), timeout=180)
        fs = [os.path.join(od, f) for f in os.listdir(od) if f.lower().endswith('.tif')]
        last = fs[0] if fs else None
    return last, r


def main():
    global L1, OUT
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile', default='kurai', help='预设 id 或 profile.json 路径')
    ap.add_argument('--out-root', default='', help='产物根（默认 paths.WORK）')
    a = ap.parse_args()
    prof = profile_io.load(a.profile)
    if prof.get('status') != 'calibrated':
        print('⚠ 预设 %s 状态=%s：五段靶带已按该预设，其他层尚未标定'
              % (prof.get('id'), prof.get('status')))
    if a.out_root:
        L1 = os.path.join(a.out_root, 'layer1')
        OUT = os.path.join(a.out_root, 'layer2')
    BR.MASTER = str(profile_io.bands_file(prof))
    LC.OUT = OUT
    import apply_layer2
    apply_layer2.L1, apply_layer2.L2 = L1, OUT
    print('预设 %s  靶带 %s  产物 %s'
          % (prof.get('name', prof.get('id')), BR.MASTER, OUT))

    rows1 = {r['stem']: r for r in
             json.load(open(os.path.join(L1, 'layer1.json'), encoding='utf-8'))['rows']}
    jobs = []
    stems = [s for s in ORDER if s in rows1] or sorted(rows1)   # 换批次时按 json 里的来
    for stem in stems:
        r1 = rows1.get(stem)
        if not r1:
            continue
        src = r1.get('src')
        if not src or not os.path.exists(src):
            print('  %s 找不到底片，跳过' % stem); continue
        ev, hl = r1.get('ev') or 0.0, r1.get('hl') or 0
        g, P = r1.get('g') or 1.0, r1.get('P') or 128.0
        mainy = curve(P, g) if (g and abs(g - 1.0) > 1e-6) else list(XS)
        jobs.append(dict(stem=stem, src=src, ev=ev, hl=hl, mainy=mainy))
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

    # ── 第一步：把每张复位到"第 1 层定稿"，导出当**唯一基准** ───────────────
    print('== 第一步：导基准（第 1 层定稿，无通道曲线）')
    for j in jobs:
        run('SELECT ' + j['src'], timeout=60)
        run('RESET ' + j['src'], timeout=60)
        if abs(j['ev']) > 0.01:
            run('GLOBAL %s Exposure2012=%.4f' % (j['src'], j['ev']), timeout=60)
        if abs(j['hl']) > 0.5:
            run('GLOBAL %s Highlights2012=%d' % (j['src'], j['hl']), timeout=60)
        run('CURVES %s %s' % (j['src'], spec(j['mainy'], IDENT, IDENT, IDENT)), timeout=120)
        f, r = expo(j['stem'] + '/base')
        if not f:
            print('  %s 基准导出失败 %s' % (j['stem'], r)); continue
        j['base'] = f
        print('  %s %s' % (j['stem'], os.path.basename(f)))
    jobs = [j for j in jobs if j.get('base')]
    if not jobs:
        print('一张基准都没导出来'); return

    # ── 第二步：**逐张迭代求解** ─────────────────────────────────────────────
    #   ★ SV 10-23 18:5x：整组一条曲线不是"保证一致"，是"保证中位好看"。
    #     实测同一条曲线砍在五张上：80-100 段红绿极差 1.4→2.0（更散）、60-80 段 7.2→7.1（没动），
    #     3024 那张 +6.5 离群 7 个点完全没管。⇒ 靶一致（同一个带），刀逐张。
    #   ★★★ 为什么是迭代，不是"算一次写完"：增益矩阵是 ±6 码阶跃标定的线性模型，
    #     亮端不够准 —— 实测 2497 的 80-100 段红绿设计该走 +1.76，实际只走 +0.8（45%）。
    #     硬修模型没用（标定本身就用了那张图）。正解是**用画面反馈修正残差**：
    #     写一次 → 量 → 解残差 → 累加到累计位移 → 再写。迭代不依赖模型的绝对精度。
    #     第二轮起只走残差的 DAMP（0.6），防来回震荡。
    ROUNDS, DAMP = 3, 0.6
    before = {}
    for j in jobs:
        j['tot'] = {nm: [0.0, 0.0, 0.0] for nm in BR.NAMES}
        j['hist'] = []
        before[j['stem']] = {b['band']: b
                             for b in BR.read(j['base'], mask_from=j['base'])['bands']}

    for rnd in range(ROUNDS):
        print('\n== 第 %d 轮' % (rnd + 1))
        moved = 0
        for j in jobs:
            cur = j.get('after') or j['base']
            rows = LC.stats_one(cur, mask=j['base'])     # 掩膜永远锁在 base
            LC.solve_rows(rows)
            need = LC.residual(rows)
            if need < 0.03:
                print('  %s 已在带内（残差 %.2f），这轮不动' % (j['stem'], need))
                continue
            damp = 1.0 if rnd == 0 else DAMP
            for r in rows:
                t = j['tot'][r['band']]
                for ci in range(3):
                    t[ci] = float(np.clip(t[ci] + damp * r['d'][ci], -12, 12))
            curves = LC.build_from_dmap({nm: tuple(v) for nm, v in j['tot'].items()})
            w = run('CURVES %s %s' % (j['src'],
                                      spec(j['mainy'], curves['R'], curves['G'], curves['B'])),
                    timeout=120)
            f, rr = expo('%s/r%d' % (j['stem'], rnd + 1))
            if not f:
                print('  %s 第%d轮导出失败 %s' % (j['stem'], rnd + 1, rr)); continue
            j['after'] = f
            j['curves'] = curves
            j['hist'].append(round(need, 2))
            moved += 1
            print('  %s 残差 %.2f ｜ 累计位移 R%+.1f/G%+.1f/B%+.1f ｜ %s'
                  % (j['stem'], need, *j['tot']['80-100'], w))
        if not moved:
            print('  整组都进带了，停')
            break

    got = []
    for j in jobs:
        if not j.get('after'):
            print('  %s 没产出改后图' % j['stem']); continue
        b1 = {b['band']: b for b in BR.read(j['after'], mask_from=j['base'])['bands']}
        got.append(dict(stem=j['stem'], src=j['src'], base=j['base'], after=j['after'],
                        curves=j.get('curves'), hist=j['hist'],
                        before=before[j['stem']], got=b1))

    mas = BR.master()
    print('\n%-8s %5s %8s %8s %8s %8s %6s'
          % ('段', 'n', 'a*前→后', 'b*前→后', '靶a', '靶b', '进带?'))
    for rec in got:
        print('-- %s' % rec['stem'])
        for nm in BR.NAMES:
            x, y = rec['before'].get(nm), rec['got'].get(nm)
            if not x or not y or 'na' not in y:
                print('%-8s  中性像素不足' % nm); continue
            m = mas[nm]
            ain = m['a_p25'] <= y['na'] <= m['a_p75']
            bin_ = m['b_p25'] <= y['nb'] <= m['b_p75']
            print('%-8s %5d %+5.1f→%+5.1f %+5.1f→%+5.1f %+6.1f %+6.1f  %s%s'
                  % (nm, y['n_neutral'], x['na'], y['na'], x['nb'], y['nb'],
                     m['a_p50'], m['b_p50'], 'a✓' if ain else 'a✗', 'b✓' if bin_ else 'b✗'))

    # ── ★ 判据：逐张离鹿井多远（进没进他的带）。不是组内互相对齐 ──────────────
    #   SV 10-23 19:1x：「谁说了整组可以一个值？…整组可以一致意思是都有鹿井的风格」
    hit, tot_cell, miss = 0, 0, []
    for rec in got:
        for nm in BR.NAMES:
            y = rec['got'].get(nm)
            if not y or 'na' not in y:
                continue
            m = mas[nm]
            tot_cell += 1
            ain = m['a_p25'] <= y['na'] <= m['a_p75']
            bin_ = m['b_p25'] <= y['nb'] <= m['b_p75']
            hit += int(ain and bin_)
            if not (ain and bin_):
                miss.append('%s/%s 红绿%s 黄蓝%s'
                            % (rec['stem'], nm, '✓' if ain else '✗',
                               '✓' if bin_ else '✗'))
    print('\n== 判据：逐张落进鹿井的带  %d/%d 段（红绿黄蓝都对上才算进）'
          % (hit, tot_cell))
    if miss:
        print('   没进：' + ' · '.join(miss))
    print('   ⇒ 一致 = 每张都像鹿井；「五张之间极差收窄」只是副产品（下表仅作体检）')

    # ── 分布体检（诊断用，不作判据）────────────────────────────────────────
    #   看的是"是不是往同一个靶上走"：改后极差 ≤ 他的带宽 ⇔ 散布水平和大师相当。
    print('\n== 分布体检（改前 vs 改后：五张之间的极差，对大师带宽）')
    print('%-8s | %5s %6s %6s | %5s %6s %6s | %s'
          % ('段', '带a宽', '前极差', '后极差', '带b宽', '前极差', '后极差', '判定'))
    summ, all_ok = [], True
    for nm in BR.NAMES:
        m = mas[nm]
        pairs = [(r['before'].get(nm), r['got'].get(nm)) for r in got]
        pairs = [(x, y) for x, y in pairs if x and y and 'na' in x and 'na' in y]
        if len(pairs) < 2:
            print('%-8s |  中性像素不足' % nm); continue
        aw = m['a_p75'] - m['a_p25']
        bw = m['b_p75'] - m['b_p25']
        a0 = [x['na'] for x, _ in pairs]; a1 = [y['na'] for _, y in pairs]
        b0 = [x['nb'] for x, _ in pairs]; b1 = [y['nb'] for _, y in pairs]
        ra0, ra1 = max(a0) - min(a0), max(a1) - min(a1)
        rb0, rb1 = max(b0) - min(b0), max(b1) - min(b1)
        # 收缩了（或本来就 ≤ 带宽）才算过
        ok = (ra1 <= aw + 1e-6 or ra1 < ra0) and (rb1 <= bw + 1e-6 or rb1 < rb0)
        all_ok = all_ok and ok
        summ.append(dict(band=nm, a_w=aw, b_w=bw,
                         a_spread_before=ra0, a_spread_after=ra1,
                         b_spread_before=rb0, b_spread_after=rb1,
                         ok=bool(ok), n=len(pairs)))
        print('%-8s | %5.1f %6.1f %6.1f | %5.1f %6.1f %6.1f | %s'
              % (nm, aw, ra0, ra1, bw, rb0, rb1,
                 ('收窄' if (ra1 < ra0 and rb1 < rb0) else '没更散') if ok else '更散/没进带'))
    print('\n分布体检：%s' % ('五段散布都不超过大师自己的水平' if all_ok else '有段比大师更散，见上表'))
    print('（体检合格不代表做对了；判据只有上面那张"进带格数"表）')

    out_files = []
    for j in got:
        bands = {}
        for nm in BR.NAMES:
            y = j['got'].get(nm)
            if not y or 'na' not in y:
                continue
            m = mas[nm]
            bands[nm] = dict(na=y['na'], nb=y['nb'],
                             ain=bool(m['a_p25'] <= y['na'] <= m['a_p75']),
                             bin=bool(m['b_p25'] <= y['nb'] <= m['b_p75']))
        out_files.append(dict(stem=j['stem'], src=j['src'], base=j['base'],
                              after=j.get('after'), curves=j.get('curves'),
                              hist=j.get('hist') or [], bands=bands))
    json.dump(dict(summary=summ, all_ok=bool(all_ok),
                   judge=dict(hit=hit, cells=tot_cell, miss=miss),
                   master={nm: dict(a_p25=mas[nm]['a_p25'], a_p50=mas[nm]['a_p50'],
                                    a_p75=mas[nm]['a_p75'], b_p25=mas[nm]['b_p25'],
                                    b_p50=mas[nm]['b_p50'], b_p75=mas[nm]['b_p75'])
                           for nm in BR.NAMES},
                   files=out_files),
              open(os.path.join(OUT, 'layer2.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1, default=float)
    print('\n-> %s' % os.path.join(OUT, 'layer2.json'))


if __name__ == '__main__':
    main()
