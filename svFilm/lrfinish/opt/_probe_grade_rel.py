# -*- coding: utf-8 -*-
r"""第 5 层（分级）第 0 步：**先量鹿井自己的"段间关系"分布**，不写求解器。

为什么要先量（而不是先定设计）：
  分级（分离色调）在画面上唯一的表现形式是**段与段之间的色偏差**，
  不是某一段的绝对色偏（绝对色偏里混着场景本身的颜色 —— §6 口径陷阱）。
  ⇒ 要问的第一个问题不是"该加多少"，而是"他那个关系到底稳不稳"。
     稳（分布窄）⇒ 是风格，值得逐张对齐；
     不稳（分布宽）⇒ 那是内容/光型，不是风格 ⇒ **零动作**，并如实报出来。

量法（与第 2 层同一把尺）：
  每张按 L 分五段，段内取"本该是灰的"像素（C ≤ 8，≥200 px）的 a*/b* 中位。
  关系量 = 某段中位 − 中调(40-60) 段中位。

数据：
  大师 = E:/ds/大师成片/鹿井/ 三个目录共 52 张原尺寸成片（**必须读磁盘**：
         refpack/lj_lab.npz 里那 747 张缩图 L 只在 42~74 之间，是被压平的缩略图，
         量不出段间关系 —— 实测确认过，别再用）
  我们 = layer2.json 里第 2 层定稿的改后 tif

产物：屏显 + E:/Debug_svStudio/lr_agent_out/2026-10-23/grade_rel.json
用法：python opt/_probe_grade_rel.py
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

import _boot  # noqa: E402
import paths
paths.ensure_import_path()
import lr_measure as M                    # noqa: E402

C_NEUTRAL = 8.0
MIN_PX = 200
BANDS = ((0, 20), (20, 40), (40, 60), (60, 80), (80, 100))
NAMES = ('0-20', '20-40', '40-60', '60-80', '80-100')
MROOT = str(paths.MASTER / '鹿井')
L2 = str(paths.WORK / 'layer2' / 'layer2.json')
OUT = str(paths.WORK / 'grade_rel.json')

# 关系量：(名字, 分子段, 分母段)
RELS = [('阴影-中调', '0-20'), ('影子-中调', '20-40'),
        ('中高-中调', '60-80'), ('亮部-中调', '80-100')]


def bands_of(rgb):
    """rgb float 0..1 → 五段的 (L,a,b) 中位（段内 C≤8 的像素）。"""
    lab = M.color.to_lab(np.ascontiguousarray(rgb))
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    C = np.hypot(a, b)
    out = {}
    for nm, (lo, hi) in zip(NAMES, BANDS):
        m = (L >= lo) & (L < hi) & (C <= C_NEUTRAL)
        if int(m.sum()) >= MIN_PX:
            out[nm] = dict(n=int(m.sum()),
                           L=float(np.median(L[m])),
                           a=float(np.median(a[m])),
                           b=float(np.median(b[m])))
    return out


def rels_of(bd):
    """→ {关系名: (Δa, Δb)}；缺段就不算。"""
    if '40-60' not in bd:
        return {}
    c = bd['40-60']
    out = {}
    for nm, seg in RELS:
        if seg in bd:
            out[nm] = (bd[seg]['a'] - c['a'], bd[seg]['b'] - c['b'])
    return out


def pct(v, q):
    return float(np.percentile(v, q)) if v else float('nan')


def dist(vals):
    return dict(n=len(vals), p10=pct(vals, 10), p25=pct(vals, 25),
                p50=pct(vals, 50), p75=pct(vals, 75), p90=pct(vals, 90),
                iqr=pct(vals, 75) - pct(vals, 25))


def main():
    # ── 大师（52 张原尺寸，必须从磁盘读；同一张可能有多份拷贝，按文件名去重）──
    import glob
    seen, paths = set(), []
    for p in sorted(glob.glob(os.path.join(MROOT, '*', '*'))):
        if os.path.splitext(p)[1].lower() not in ('.jpg', '.jpeg', '.tif', '.tiff', '.png'):
            continue
        key = (os.path.dirname(p), os.path.splitext(os.path.basename(p))[0])
        if key in seen:
            continue
        seen.add(key)
        paths.append(p)
    per_img, marg = [], {nm: {'a': [], 'b': []} for nm in NAMES}
    for p in paths:
        bd = bands_of(M.load_rgb(p, 1024))
        for nm in NAMES:
            if nm in bd:
                marg[nm]['a'].append(bd[nm]['a'])
                marg[nm]['b'].append(bd[nm]['b'])
        r = rels_of(bd)
        if r:
            per_img.append((os.path.basename(p), r))

    print('== 大师 %d 张里，能量出关系的 %d 张' % (len(paths), len(per_img)))
    print('\n-- 各段绝对 a*/b* 中位（校验：对不对得上 style_kurai.json 的口径）')
    sky = json.load(open(str(paths.PRESETS / '鹿井预设' / 'bands.json'),
                         encoding='utf-8'))['keys']
    print('%-8s %-22s %-22s' % ('段', '这次算的 a* P25/P50/P75', 'style_kurai 的'))
    marg_out = {}
    for nm in NAMES:
        d_a = dist(marg[nm]['a'])
        d_b = dist(marg[nm]['b'])
        marg_out[nm] = dict(a=d_a, b=d_b)
        ka = sky.get('NAa' + nm.replace('-', '_'), {})
        kb = sky.get('NAb' + nm.replace('-', '_'), {})
        print('%-8s a %+5.1f/%+5.1f/%+5.1f   b %+5.1f/%+5.1f/%+5.1f   | %+5.1f/%+5.1f/%+5.1f · %+5.1f/%+5.1f/%+5.1f'
              % (nm, d_a['p25'], d_a['p50'], d_a['p75'],
                 d_b['p25'], d_b['p50'], d_b['p75'],
                 ka.get('p25', float('nan')), ka.get('p50', float('nan')), ka.get('p75', float('nan')),
                 kb.get('p25', float('nan')), kb.get('p50', float('nan')), kb.get('p75', float('nan'))))

    # ── 关系分布（这是第 5 层真正要问的问题）──────────────────────────
    rel_out = {}
    print('\n-- 段间关系（该段 − 中调 40-60 段）')
    print('%-10s %5s | %-26s | %-26s | %s'
          % ('关系', 'n', 'Δa* P10/25/50/75/90', 'Δb* P10/25/50/75/90', '方向一致率'))
    for nm, _ in RELS:
        va = [r[nm][0] for _, r in per_img if nm in r]
        vb = [r[nm][1] for _, r in per_img if nm in r]
        da, db = dist(va), dist(vb)
        # 方向一致率：他这套到底有没有"固定的方向"，还是两边乱跑
        if nm == '阴影-中调' or nm == '影子-中调':
            fa = 100.0 * np.mean(np.array(va) < 0)      # 比中调更绿（a 更低）
            fb = 100.0 * np.mean(np.array(vb) < 0)      # 比中调更冷（b 更低）
        elif nm == '亮部-中调':
            fa = 100.0 * np.mean(np.array(va) > 0)      # 亮部更红
            fb = 100.0 * np.mean(np.array(vb) < 0)      # 亮部更冷
        else:
            fa = 100.0 * np.mean(np.array(va) > 0)
            fb = 100.0 * np.mean(np.array(vb) < 0)
        rel_out[nm] = dict(a=da, b=db, agree_a=float(fa), agree_b=float(fb))
        print('%-10s %5d | %+5.1f %+5.1f %+5.1f %+5.1f %+5.1f | %+5.1f %+5.1f %+5.1f %+5.1f %+5.1f | a %3.0f%% b %3.0f%%'
              % (nm, len(va), da['p10'], da['p25'], da['p50'], da['p75'], da['p90'],
                 db['p10'], db['p25'], db['p50'], db['p75'], db['p90'], fa, fb))

    # ── 这条最关键：关系的散布 vs 单段自己的带宽 ────────────────────
    print('\n-- 关系散布 有多宽？（IQR 对比"该段自己的 P25~P75 宽度"）')
    print('%-10s %-16s %-16s %s' % ('关系', 'Δa IQR', 'Δb IQR', '单段带宽 a / b'))
    for nm, seg in RELS:
        wa = marg[seg]['a'] and (pct(marg[seg]['a'], 75) - pct(marg[seg]['a'], 25))
        wb = pct(marg[seg]['b'], 75) - pct(marg[seg]['b'], 25)
        ia, ib = rel_out[nm]['a']['iqr'], rel_out[nm]['b']['iqr']
        print('%-10s %-16s %-16s %.1f / %.1f   →  %.1f× / %.1f×'
              % (nm, '%+.1f' % ia, '%+.1f' % ib, wa, wb, ia / max(wa, 1e-6), ib / max(wb, 1e-6)))

    # ── 我们五张现在站在哪 ────────────────────────────────────────
    d2 = json.load(open(L2, encoding='utf-8'))
    mine = []
    print('\n-- 我们五张（第 2 层定稿）')
    print('%-10s %-10s %8s %8s   %s' % ('哪张', '关系', 'Δa*', 'Δb*', '在他 P25~P75 里？'))
    for rec in d2['files']:
        p = rec.get('after')
        if not p or not os.path.exists(p):
            print('%-10s 改后图缺失' % rec['stem']); continue
        bd = bands_of(M.load_rgb(p, 1024))
        r = rels_of(bd)
        row = dict(stem=rec['stem'], rels={})
        for nm, _ in RELS:
            if nm not in r:
                continue
            va, vb = r[nm]
            row['rels'][nm] = dict(a=va, b=vb)
            ma, mb = rel_out[nm]['a'], rel_out[nm]['b']
            ain = ma['p25'] <= va <= ma['p75']
            bin_ = mb['p25'] <= vb <= mb['p75']
            print('%-10s %-10s %+8.1f %+8.1f   %s%s'
                  % (rec['stem'], nm, va, vb, 'a✓' if ain else 'a✗', 'b✓' if bin_ else 'b✗'))
        mine.append(row)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(dict(master_n=len(per_img), marginal=marg_out, relation=rel_out,
                   ours=mine), open(OUT, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('\n-> %s' % OUT)


if __name__ == '__main__':
    main()
