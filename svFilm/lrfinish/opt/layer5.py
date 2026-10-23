# -*- coding: utf-8 -*-
r"""第 5 层（分级）：**逐张**给一点点"他的分离色调"，靶 = 鹿井，刀 = 这张自己。

SV 10-23 19:3x 选的是 **B（给一点点胶味）**：
  「按他那个红绿方向小步加（中调/亮部略暖、暗部略冷），幅度取他自己的三分之一左右」

★★★ 为什么只走三分之一（§17 实测）：
  他的段间关系本身散布就很宽（IQR = 单段带宽的 0.5~3.4 倍，冷暖方向一致率只有 50~69%）。
  ⇒ 那个关系**不能当硬靶**，直接对齐等于拿场景统计当靶、还会跟逆光打架。
  走三分之一 = 借他一点味道，但绝不把这张自己的光洗掉。

算法（逐张，闭环）：
① 现在在哪：五段近中性 (L,a*,b*) 中位 → 关系 rel(段) = (a(段)−a(中调), b(段)−b(中调))
② 靶：target = 现在 + (他的 rel − 现在) / 3，单段位移上限 ±0.6（再多就不是"一点点"了）
③ 解：Δrel = A · [sShadow, sHighlight]ᵀ，A 来自 `_cal_split_gain.py` 实测（每 1 格饱和度）
      —— 注意 rel 是差值，所以要用 (该段增益 − 中调段增益)
④ 写：GLOBAL（曝光/高光/分离色调）+ CURVES（四条）一次重建；导出复量；残差再解一轮
⑤ 护栏：肤色（脸框）a*/b* 位移记账；40-60 中调位移必须很小（它不该被分级带动）

用法：python opt/layer5.py
产物：<OUT>/<stem>/e<ns>/*.tif + layer5.json + 屏显
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

HERE = r'E:\lr_agent\opt'
sys.path.insert(0, HERE)
sys.path.insert(0, r'E:\lr_agent\prototype')

import lr_boot                                # noqa: E402
import lr_measure as M                        # noqa: E402
from apply_layer2 import curve, spec, XS, IDENT   # noqa: E402
from bridge_client import run                 # noqa: E402
from _probe_grade_rel import bands_of, NAMES  # noqa: E402
from _probe_clip import face_box              # noqa: E402

ROOT = os.environ.get('LR_ROOT') or r'E:\Debug_svStudio\lr_agent_out\2026-10-23'
L1 = os.path.join(ROOT, 'layer1')
L2 = os.path.join(ROOT, 'layer2')
# 这两个是**与他 52 张的基线**、和标定，跟批次无关 ⇒ 永远用 1023 那份
GR = r'E:\Debug_svStudio\lr_agent_out\2026-10-23\grade_rel.json'
GAIN = r'E:\Debug_svStudio\lr_agent_out\2026-10-23\layer5\split_gain.json'
OUT = os.path.join(ROOT, 'layer5')
ORDER = ('DSCF2532', 'DSCF3024', 'DSCF2891', 'DSCF2497', 'DSCF2572')

SHADOW_HUE, HIGH_HUE = 220, 30      # 标定出来的：暗部蓝（cos 0.97）/ 高光橙
WHEEL_SEGS = dict(shadow=('0-20', '20-40'), highlight=('60-80', '80-100'))
FACTOR = 1.0 / 3.0                  # 「走三分之一」
CLAMP = 0.6                         # 单段位移上限（a* 与 b* 各自）
SAT_MAX = 5.0                       # 饱和度上限：5 格以下皮肤位移约 ≤0.5，再多就不是"一点点"
SKIN_MAX = 0.6                      # 皮肤（脸框）a*/b* 位移护栏：超了就按比例缩轮子
DAMP = 0.7


def expo(tag, edge=2048):
    last = None
    for _ in (1, 2):
        od = os.path.join(OUT, tag, 'e%d' % time.time_ns())
        os.makedirs(od, exist_ok=True)
        r = run('EXPORTTIF %s %d' % (od, edge), timeout=180)
        fs = [os.path.join(od, f) for f in os.listdir(od) if f.lower().endswith('.tif')]
        last = fs[0] if fs else None
    return last, r


def write_state(src, ev, hl, mainy, crv, sS, sH):
    g = ('Exposure2012=%.4f,Highlights2012=%.1f,'
         'SplitToningShadowHue=%d,SplitToningShadowSaturation=%.1f,'
         'SplitToningHighlightHue=%d,SplitToningHighlightSaturation=%.1f,'
         'SplitToningBalance=0' % (ev, hl, SHADOW_HUE, sS, HIGH_HUE, sH))
    r1 = run('GLOBAL %s %s' % (src, g), timeout=90)
    r2 = run('CURVES %s %s' % (src, spec(mainy, crv['R'], crv['G'], crv['B'])), timeout=120)
    return r1, r2


def rel_of(bands):
    """{段: (Δa, Δb)}，相对中调 40-60。"""
    if '40-60' not in bands:
        return {}
    c = bands['40-60']
    return {nm: (bands[nm]['a'] - c['a'], bands[nm]['b'] - c['b'])
            for nm in NAMES if nm in bands and nm != '40-60'}


def face_ab(path):
    """脸框里 a*/b* 的中位（护栏用：分级不该把肤色带跑）。"""
    rgb = M.load_rgb(path, 1024)
    lab = M.color.to_lab(np.ascontiguousarray(rgb))
    box = face_box(rgb)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    a = lab[y0:y1, x0:x1, 1]
    b = lab[y0:y1, x0:x1, 2]
    if a.size < 200:
        return None
    return float(np.median(a)), float(np.median(b))


def main():
    r1s = {x['stem']: x for x in
           json.load(open(os.path.join(L1, 'layer1.json'), encoding='utf-8'))['rows']}
    f2s = {x['stem']: x for x in
           json.load(open(os.path.join(L2, 'layer2.json'), encoding='utf-8'))['files']}
    gr = json.load(open(GR, encoding='utf-8'))['relation']
    gain = json.load(open(GAIN, encoding='utf-8'))['states']

    # 增益：{轮: {段: (da/格, db/格)}}
    G = {}
    for st in gain:
        d = {}
        for r in st['rows']:
            d[r['band']] = (r['da'] / st['sat'], r['db'] / st['sat'])
        G[st['wheel'] + str(st['hue'])] = d
    gS = G['shadow%d' % SHADOW_HUE]
    gH = G['highlight%d' % HIGH_HUE]

    # 他的中位关系（关系名 → 数值；再映射到段名）
    HIS = {nm: (gr[nm]['a']['p50'], gr[nm]['b']['p50']) for nm in gr}
    RELMAP = {'0-20': '阴影-中调', '20-40': '影子-中调',
              '60-80': '中高-中调', '80-100': '亮部-中调'}

    jobs = []
    stems = [s for s in ORDER if s in f2s] or sorted(f2s)      # 换批次时按 json 里的来
    for stem in stems:
        a1, f2 = r1s.get(stem), f2s.get(stem)
        if not a1 or not f2 or not a1.get('src'):
            print('  %s 缺数据，跳过' % stem); continue
        ev, hl = a1.get('ev') or 0.0, a1.get('hl') or 0
        g, P = a1.get('g') or 1.0, a1.get('P') or 128.0
        jobs.append(dict(stem=stem, src=a1['src'], ev=ev, hl=hl,
                         mainy=curve(P, g) if (g and abs(g - 1.0) > 1e-6) else list(XS),
                         crv=f2['curves'], sS=0.0, sH=0.0, hist=[]))
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

    for j in jobs:
        run('SELECT ' + j['src'], timeout=60)
        write_state(j['src'], j['ev'], j['hl'], j['mainy'], j['crv'], 0, 0)
        f, _ = expo(j['stem'] + '/base')
        if not f:
            print('  %s 基准导出失败' % j['stem']); continue
        j['base'] = f
        j['bands0'] = bands_of(M.load_rgb(f, 1024))
        print('%s 基准 %s' % (j['stem'], os.path.basename(f)))
    jobs = [j for j in jobs if j.get('base')]

    # ── 逐张：解 → 写 → 量 ────────────────────────────────────────
    #   ★ 只解一轮，不追残差（10-23 19:5x 实测教训）：
    #     两轮版里 3024 的残差反而从 2.66 涨到 4.91 —— 因为它的影子段需要往 a* 正方向走，
    #     而两个轮子都只能往负方向，解不出来；第二轮还在拿高光轮硬凑，把皮肤推暖了 1.5。
    #     ⇒ 解不出来的就记下来报出，不许拿另一个轮子硬补。
    #   ★ 靶只取 0-20 / 20-40 / 80-100：**60-80 是脸所在的段**，拿它当靶等于直接给皮肤上色。
    TBAND = ('0-20', '20-40', '80-100')
    for j in jobs:
        j['rel0'] = rel_of(j['bands0'])
        tgt = {}
        for nm in TBAND:
            if nm not in j['rel0']:
                continue
            a0, b0 = j['rel0'][nm]
            ha, hb = HIS.get(RELMAP.get(nm, ''), (a0, b0))
            tgt[nm] = (a0 + max(-CLAMP, min(CLAMP, (ha - a0) * FACTOR)),
                       b0 + max(-CLAMP, min(CLAMP, (hb - b0) * FACTOR)))
        j['tgt'] = tgt
        if not tgt:
            print('  %s 没有可解的段' % j['stem']); continue

        A, y = [], []
        for nm, (ta, tb) in tgt.items():
            a0, b0 = j['rel0'][nm]
            for need, gd in ((ta - a0, 0), (tb - b0, 1)):
                A.append([gS[nm][gd] - gS['40-60'][gd],
                          gH[nm][gd] - gH['40-60'][gd]])
                y.append(need)
        A, y = np.array(A, float), np.array(y, float)
        sol, *_ = np.linalg.lstsq(A, y, rcond=None)
        cur = [float(np.clip(v, 0, SAT_MAX)) for v in sol]

        # ★ 一轮解 + 一次**只往回收**的修正（10-23 20:1x 实测）：
        #   两轮"追残差"不收敛 —— 两个轮子只有 2 个自由度，却有 6 个约束
        #   （3 段 × 2 轴），而且轮子增益随这张自己的明暗分布变。
        #   追残差会把 2891 的暗部从 −0.4 推到 −1.1（他的中位才 −1.2，等于冲过头），
        #   而 2532 那种解不出来的段会被高光轮硬凑，把皮肤推暖 0.8。
        #   ⇒ 只解一次；然后哪只轮子**冲过头**就按比例收回来（只减不增）。
        write_state(j['src'], j['ev'], j['hl'], j['mainy'], j['crv'], cur[0], cur[1])
        f, _ = expo(j['stem'] + '/g1')
        if not f:
            print('  %s 导出失败' % j['stem']); continue
        now = rel_of(bands_of(M.load_rgb(f, 1024)))

        fixed = {}
        for wheel, segs, hi in (('sS', WHEEL_SEGS['shadow'], 0), ('sH', WHEEL_SEGS['highlight'], 1)):
            tgt_v, got_v = [], []
            for nm in segs:
                if nm in tgt and nm in now:
                    tgt_v += [tgt[nm][0] - j['rel0'][nm][0], tgt[nm][1] - j['rel0'][nm][1]]
                    got_v += [now[nm][0] - j['rel0'][nm][0], now[nm][1] - j['rel0'][nm][1]]
            if not tgt_v:
                continue
            tv, gv = np.array(tgt_v), np.array(got_v)
            if np.linalg.norm(gv) < 0.15:
                continue
            k = float(np.dot(tv, gv) / np.dot(gv, gv))       # 冲过头时 k<1
            fixed[wheel] = max(0.3, min(1.0, k))
        if any(v < 0.95 for v in fixed.values()):
            cur = [cur[0] * fixed.get('sS', 1.0), cur[1] * fixed.get('sH', 1.0)]
            write_state(j['src'], j['ev'], j['hl'], j['mainy'], j['crv'], cur[0], cur[1])
            f2, _ = expo(j['stem'] + '/g1c')
            if f2:
                f = f2
                now = rel_of(bands_of(M.load_rgb(f, 1024)))
            print('  %s 收：暗×%.2f 亮×%.2f' % (j['stem'],
                                            fixed.get('sS', 1.0), fixed.get('sH', 1.0)))
        j['after'], j['now'] = f, now
        j['sS'], j['sH'] = cur[0], cur[1]

        fa0, fa1 = face_ab(j['base']), face_ab(j['after'])
        dfa = None if not (fa0 and fa1) else (fa1[0] - fa0[0], fa1[1] - fa0[1])

        # 护栏：皮肤被带太多 ⇒ 按比例缩轮子，重导一次
        if dfa and max(abs(dfa[0]), abs(dfa[1])) > SKIN_MAX:
            k = SKIN_MAX / max(abs(dfa[0]), abs(dfa[1]))
            sS, sH = j['sS'] * k, j['sH'] * k
            write_state(j['src'], j['ev'], j['hl'], j['mainy'], j['crv'], sS, sH)
            f2, _ = expo(j['stem'] + '/gk')
            if f2:
                j['after'] = f2
                j['now'] = rel_of(bands_of(M.load_rgb(f2, 1024)))
                fa2 = face_ab(f2)
                dfa = None if not (fa0 and fa2) else (fa2[0] - fa0[0], fa2[1] - fa0[1])
            j['sS'], j['sH'] = sS, sH
            j['scaled'] = round(k, 2)
            print('  %s 皮肤被带 %.1f ⇒ 轮子缩到 %.0f%%，重导'
                  % (j['stem'], SKIN_MAX / k, k * 100))
        j['face_d'] = dfa

    # ── 复量 + 护栏 ────────────────────────────────────────────────
    print('\n%-10s %6s %6s | %-44s | %s'
          % ('张', '暗轮', '亮轮', '关系 改前→改后（a/b）', '肤色 Δa/Δb'))
    summ = []
    for j in jobs:
        if not j.get('after') or not j.get('now'):
            continue
        now, dfa = j['now'], j.get('face_d')
        pair = []
        for nm in ('0-20', '20-40', '80-100'):
            if nm in now and nm in j['rel0']:
                pair.append('%s %+.1f/%+.1f→%+.1f/%+.1f'
                            % (nm, j['rel0'][nm][0], j['rel0'][nm][1], now[nm][0], now[nm][1]))
        print('%-10s %6.1f %6.1f | %-44s | %s'
              % (j['stem'], j['sS'], j['sH'], ' ｜ '.join(pair),
                 '皮肤 Δa %+.2f Δb %+.2f' % dfa if dfa else '皮肤 量不到'))
        summ.append(dict(stem=j['stem'], sS=j['sS'], sH=j['sH'],
                         rel0=j['rel0'], tgt=j['tgt'], now=now,
                         scaled=j.get('scaled'), face_d=list(dfa) if dfa else None,
                         base=j['base'], after=j['after']))

    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, 'layer5.json')
    json.dump(dict(shadow_hue=SHADOW_HUE, high_hue=HIGH_HUE, factor=FACTOR,
                   clamp=CLAMP, sat_max=SAT_MAX, files=summ),
              open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n-> %s' % p)


if __name__ == '__main__':
    main()
