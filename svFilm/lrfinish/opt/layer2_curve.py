# -*- coding: utf-8 -*-
r"""第 2 层求解器（**逐张**）：每张底片算自己的一条通道曲线。

★★★ 10-23 18:5x SV 纠偏（这一层的设计被推翻一次）：
  「求解器的意思不就是你要对每张照片去计算出最好的色调曲线和颜色分级吗？
    虽然是同一组照片但是场景不一样 光线条件构图也不一样啊。
    风格当然是需要保证整组一致 因为是发到社交圈」
  ⇒ 我把「一致」理解成了**用同一组参数**，那是反的：
    同一条曲线砍在不同场景上，出来的风格反而更散（起点不同、需要的量不同，
    一刀下去有的砍过头有的没砍够）。实测证据（本组五张，80-100 段红绿 a*）：
    改前极差 1.4 → 改后极差 **2.0（更散）**；60-80 段极差 7.2 → 7.1（一点没动），
    3024 那张 +6.5 离群 7 个点，整组一条曲线完全没管住它。
  ⇒ **靶一致，刀逐张**：整组共用一个"靶"（大师的带），每张各自解要挪几码。
    这跟印相一个道理：相纸和显影时间固定（介质 = 风格），每张底片曝光时间各自定（正确）。

口径（都来自已定的规矩，不是新拍的）：
  · **量具 = band_read**：每段只取"本该是灰的"像素（C ≤ 8），读三通道码值中位。
    掩膜**锁在这张图自己**（`mask_from=p`）。
  · **判据 = 出没出带**：大师逐段 a*/b* 的 [P25,P75] 就是带。
  · **目标 = 带内落点**（clamp 到 [P25+10%带宽, P75−10%带宽]）。
  · **解 = 实测增益矩阵**（不是 from_lab 反解，它把 R 高估 4 倍），第一行约束 dL=0。
  · **一处一刀 = 平台，不是尖峰**：段内两点同偏移（连线 y=x+d 即纯平移）。共 12 点（LR 上限 20）。
  · **增益按实际亮度插值**：同一段在不同图上 L* 不同（3024 抬过曝光），固定段值会偏。

用法：python opt/layer2_curve.py <tif> [tif ...]        # 逐张算，只屏显不写 LR
产物：<OUT>/layer2_curve.json
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = r'E:\lr_agent\opt'
sys.path.insert(0, HERE)
sys.path.insert(0, r'E:/svStudio/svFilm')

import band_read as BR          # noqa: E402

OUT = r'E:\Debug_svStudio\lr_agent_out\2026-10-23\layer2'
XS = list(range(0, 256, 16))
MARGIN = 0.10                                  # 目标落点往带内缩 10% 带宽
MAX_SHIFT = 8.0                                # 单张单段单通道最多挪几码（增益矩阵是 ±6 码标定的）
CENTERS = (10.0, 30.0, 50.0, 70.0, 90.0)       # 五段标定时的 L* 中心

# ★★★★★ 增益矩阵（10-23 20:27 实测，`opt/_cal_curve_gain.py`，单通道 +6 码阶跃）：
#   GAIN[段][通道] = (dL*, da*, db*) 每挪 1 码的效应。
#   为什么必须实测：`from_lab` 反解说"要让 a* 走 0.9 该挪 R 3.1 码"，照做实测走了 +4.6
#   （高估 R 约 4 倍）。用本矩阵验算同一刀应产生 Δa=+4.48，实测 +4.6 ⇒ 矩阵准。
#   顺带：① 通道曲线几乎不改亮度（dL 全在 ±0.28 内）⇒ 只改色不改亮；
#          ② 同一通道效应随亮度变（da/dR 暗部 0.86 → 亮部 1.29）⇒ 必须按实际 L* 插值。
GAIN = {
    '0-20':   dict(R=(+0.060, +0.998, +0.203), G=(+0.176, -1.264, +0.601), B=(+0.004, +0.148, -0.734)),
    '20-40':  dict(R=(+0.018, +0.993, +0.198), G=(+0.055, -1.135, +0.561), B=(+0.001, +0.151, -0.736)),
    '40-60':  dict(R=(-0.006, +0.888, +0.178), G=(-0.020, -1.011, +0.496), B=(-0.000, +0.126, -0.662)),
    '60-80':  dict(R=(-0.028, +0.858, +0.172), G=(-0.073, -0.979, +0.481), B=(-0.004, +0.121, -0.643)),
    '80-100': dict(R=(+0.082, +1.293, +0.265), G=(+0.274, -1.463, +0.721), B=(+0.005, +0.165, -0.881)),
}


def L_to_code(L):
    Y = ((L + 16.0) / 116.0) ** 3 if L > 8.0 else L / 903.3
    v = 12.92 * Y if Y <= 0.0031308 else 1.055 * Y ** (1 / 2.4) - 0.055
    return float(np.clip(v, 0.0, 1.0) * 255.0)


def gain_at(L):
    """按这张图在该段的**实际 L\\*** 在标定段中心之间线性插值。

    为什么：增益随亮度变（da/dR 0.86→1.29），而同一段在不同图上 L* 不同
    （3024 抬过曝光、2532 压过曝光）。固定用段名的值，等于拿别人的亮度算自己的账。
    """
    names = BR.NAMES
    if L is None or not np.isfinite(L):
        return GAIN[names[2]]
    if L <= CENTERS[0]:
        return GAIN[names[0]]
    if L >= CENTERS[-1]:
        return GAIN[names[-1]]
    for i in range(len(CENTERS) - 1):
        if CENTERS[i] <= L <= CENTERS[i + 1]:
            t = (L - CENTERS[i]) / (CENTERS[i + 1] - CENTERS[i])
            g0, g1 = GAIN[names[i]], GAIN[names[i + 1]]
            return {c: tuple(g0[c][j] * (1 - t) + g1[c][j] * t for j in range(3))
                    for c in 'RGB'}
    return GAIN[names[2]]


def solve_d(g, da, db):
    """解 3×3：三通道各挪几码，才能让 (L, a*, b*) 变成 (L, a+da, b+db)。

    第一行 rhs=0 ⇒ 约束 **dL=0**，让通道曲线不偷改亮度（亮度归第 1 层）。
    解不开（矩阵退化）就返回零动作 —— 宁可不做，不要瞎挪。
    """
    A = np.array([[g[c][0] for c in 'RGB'],
                  [g[c][1] for c in 'RGB'],
                  [g[c][2] for c in 'RGB']], dtype=np.float64)
    rhs = np.array([0.0, da, db], dtype=np.float64)
    try:
        d = np.linalg.solve(A, rhs)
    except np.linalg.LinAlgError:
        return (0.0, 0.0, 0.0)
    return tuple(float(v) for v in np.clip(d, -MAX_SHIFT, MAX_SHIFT))


def into_band(v, lo, hi):
    """把 v 放进 [lo,hi]，离边留 MARGIN 的带宽。

    ★ 是"带边界"不是"带中心"：判据是**出没出带**（区间）。走到边界 = 最小改动就进带，
      也不可能冲过中心。"走一半"/"走到中心"是从"靶是单点"里来的，区间很窄时走一半永远停在半路。
    """
    if hi <= lo:
        return float(lo)
    m = MARGIN * (hi - lo)
    return float(np.clip(v, lo + m, hi - m))


def stats_one(path, mask=None):
    """单张五段近中性 (L,a*,b*) 中位 + 大师的带。

    mask 给了就用它当掩膜（**必须**：迭代时量的是"改后"那张图，但掩膜要锁在基准上，
    否则一批像素进出掩膜，读的就是另一批人了）。
    """
    r = BR.read(path, mask_from=mask or path)
    mas = BR.master()
    rows = []
    for k, nm in enumerate(BR.NAMES):
        b = r['bands'][k]
        m = mas[nm]
        row = dict(band=nm, ok=('na' in b),
                   L=b.get('L'), na=b.get('na'), nb=b.get('nb'),
                   R=b.get('R'), G=b.get('G'), B=b.get('B'),
                   n=int(b.get('n_neutral', 0)),
                   master=dict(a=m['a_p50'], b=m['b_p50'],
                               a_w=(m['a_p25'], m['a_p75']),
                               b_w=(m['b_p25'], m['b_p75'])))
        rows.append(row)
    return rows


def solve_rows(rows):
    """给每段填 d（三通道要挪几码）和 act（人话）。"""
    for r in rows:
        if not r['ok']:
            r['act'] = '中性像素不足，不动'
            r['d'] = (0.0, 0.0, 0.0)
            r['in_a'] = r['in_b'] = False
            continue
        aw, bw = r['master']['a_w'], r['master']['b_w']
        r['in_a'] = bool(aw[0] <= r['na'] <= aw[1])
        r['in_b'] = bool(bw[0] <= r['nb'] <= bw[1])
        a_t = into_band(r['na'], *aw)
        b_t = into_band(r['nb'], *bw)
        da, db = a_t - r['na'], b_t - r['nb']
        if abs(da) < 0.05 and abs(db) < 0.05:
            r['act'] = '已在带内'
            r['d'] = (0.0, 0.0, 0.0)
            continue
        g = gain_at(r['L'])
        r['d'] = solve_d(g, da, db)
        r['tgt_a'], r['tgt_b'] = a_t, b_t
        # 增益模型预测（和 d 同一套）
        r['pred_a'] = r['na'] + sum(g[c][1] * r['d'][i] for i, c in enumerate('RGB'))
        r['pred_b'] = r['nb'] + sum(g[c][2] * r['d'][i] for i, c in enumerate('RGB'))
        # 位移吃完还到不了 ⇒ 这一刀顶到上限了：要么归上游，要么这张不该接
        short = max(abs(r['pred_a'] - a_t), abs(r['pred_b'] - b_t))
        r['act'] = ('出带' + ('a' if not r['in_a'] else '') + ('b' if not r['in_b'] else '')
                    + ('(顶上限,还差%.1f)' % short if short > 0.15 else ''))
    return rows


def build_curves(rows):
    """五段平台 → 12 点曲线。段内两点同偏移 ⇒ 段内严格平移；只钉黑端，白端跟段 5 走。

    ★★★★★ 点数上限 20（10-23 实测，`opt/_probe_curvefmt.py`）：LR 的曲线点对数组最多 20 点，
      超了**静默截断到前 20 点**。证据：25 点写进去 `readback` = 4 通道 × 40 数 = 20 点/通道，
      曲线停在 (194,197)，码 194 以上压到 197 ⇒ Lmax 98→81、80-95 段 32%→0。
      对照：2 / 16（缺 255）/ 17 点恒等全部零变化。
    """
    edges = [int(round(L_to_code(L))) for L in (20.0, 40.0, 60.0, 80.0)]
    lim = [(0, edges[0]), (edges[0], edges[1]), (edges[1], edges[2]),
           (edges[2], edges[3]), (edges[3], 255)]
    PAD = 8
    probes = []
    for k, (lo, hi) in enumerate(lim):
        a = PAD if k == 0 else lo + PAD
        b = 247 if k == 4 else hi - PAD
        probes += [(k, a), (k, b)]

    curves = {}
    for ci, ch in enumerate('RGB'):
        pts = [(0, 0)]
        for k, x in probes:
            pts.append((x, x + rows[k]['d'][ci]))
        pts.append((255, 255 + rows[4]['d'][ci]))
        clean, prev_x = [], -1
        for x, y in pts:
            y = int(round(min(255, max(0, y))))
            if x <= prev_x:
                continue
            if clean and y <= clean[-1][1]:
                y = clean[-1][1] + 1
            clean.append((x, y)); prev_x = x
        if len(clean) > 20:
            raise SystemExit('%s 曲线 %d 点 > LR 上限 20 ⇒ 会被静默截断，拒绝写入'
                             % (ch, len(clean)))
        curves[ch] = clean
    return curves


def build_from_dmap(dmap):
    """dmap = {段名: (dR,dG,dB)} → 12 点曲线。

    ★ 迭代要用它而不是 `build_curves(rows)`：迭代维护的是**累计位移**，
      不是"这一轮的残差"。曲线必须是"基态 → 累计位移"，否则第二轮会把第一轮抹掉。
    """
    return build_curves([dict(d=tuple(dmap[nm])) for nm in BR.NAMES])


def plan_one(path):
    """单张的全套：量 → 解 → 拼曲线。"""
    rows = stats_one(path)
    solve_rows(rows)
    return rows, build_curves(rows)


def residual(rows):
    """这一轮还差多少（a* 和 b* 各自离目标落点有多远）。全在带内 ⇒ 0。"""
    w = 0.0
    for r in rows:
        if not r['ok']:
            continue
        ta = r.get('tgt_a', r['na'])
        tb = r.get('tgt_b', r['nb'])
        w = max(w, abs(ta - r['na']), abs(tb - r['nb']))
    return w


def show_one(tag, rows, curves):
    print('\n-- %s' % tag)
    print('%-8s %5s %6s %6s %6s | %6s %6s | %-22s %s'
          % ('段', 'n', 'L*', 'a*', 'b*', '靶a', '靶b', '判断', '平移 dR/dG/dB'))
    for r in rows:
        if not r['ok']:
            print('%-8s %5d  %s' % (r['band'], r['n'], r['act']))
            continue
        m = r['master']
        print('%-8s %5d %6.1f %+6.1f %+6.1f | %+6.1f %+6.1f | %-22s %+5.2f/%+5.2f/%+5.2f'
              % (r['band'], r['n'], r['L'], r['na'], r['nb'], m['a'], m['b'],
                 r['act'], r['d'][0], r['d'][1], r['d'][2]))
    print('  曲线  ' + '  '.join('%s %d点' % (ch, len(curves[ch])) for ch in 'RGB'))


def main():
    files = [p for p in sys.argv[1:] if os.path.exists(p)]
    if not files:
        print('用法: layer2_curve.py <tif> [tif ...]'); return
    os.makedirs(OUT, exist_ok=True)
    out = []
    for p in files:
        rows, curves = plan_one(p)
        show_one(os.path.basename(p), rows, curves)
        out.append(dict(file=p, rows=rows, curves=curves))
    json.dump(out, open(os.path.join(OUT, 'layer2_curve.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1, default=float)
    print('\n-> %s' % os.path.join(OUT, 'layer2_curve.json'))


if __name__ == '__main__':
    main()
