# -*- coding: utf-8 -*-
"""批次级求解器（**按规范实现**：`lr_basic_curve_grading.md` §3.2/§4/§7 + ADR-001..006）。

与 `solve5.py` 的区别，就是规范与"我上一版"的区别：
  · 一批**一个 EV**（不是逐图）；单张出带 ⇒ 标 outlier，不拉参数。
  · 先过**批次离散度门**：批内脸 L 的 IQR > 15 L* ⇒ 判 `batch_too_dispersed`，拒绝求解。
  · **一条曲线**给整批（不是逐图）；锚点用**全图**分位 p5/p25/p50/p75/p95（ADR-002），
    在 **sRGB code 空间**过 5 点 + 端点 (0,0)/(1,1)，单调、限斜率。
  · 一组 HSL 给整批（肤色 h/C 的批次中位残差）。
  · EV 用**亮度比** `log2(Y_t/Y_0)`，clamp ±1.5（ADR-003）。

不渲整批：默认只解释要做什么（`--dry`），`--go` 才真跑。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, r'E:\ds\photo\_corpus\kurai\full')
import lrstate as LS                                    # noqa: E402
from measure import measure                             # noqa: E402

BASE = r'E:\ds\photo\_corpus\kurai\full'
DISPERSION_MAX = 15.0        # L*；≈1.5 个带宽（ADR-001）
EV_CLAMP = 1.5               # ADR-003
SLOPE_MAX = 3.0              # §4.3


# ---------- 颜色空间小工具：L* ↔ Y ↔ sRGB code ----------
def lstar_to_y(L):
    L = np.asarray(L, float) / 100.0
    return np.where(L > 0.08, ((L + 0.16) / 1.16) ** 3, L / 9.033)


def y_to_code(y):
    y = np.clip(np.asarray(y, float), 0.0, 1.0)
    s = np.where(y <= 0.0031308, 12.92 * y, 1.055 * y ** (1 / 2.4) - 0.055)
    return s * 255.0


def lstar_to_code(L):
    return y_to_code(lstar_to_y(L))


def normalize_anchors(xq, gt, cap=97.0):
    """ADR-007：曲线只搬**形状**，不搬截距。

    `y_q = x_q + (卡的q − 卡的p50) − (批次的q − 批次的p50)`
    ⇒ p50 锚点恒等；其余四个只贡献相对形状。
    不这么做的话，曝光刚把脸推到靶位，曲线又会按卡的**绝对** p50 把中间调压回去
    （实测 919：批 p50 70.6 / 卡 p50 56.3 ⇒ 中间调 −14.3 L*，带内 0/3）。

    ★ 白爆顶帽（2026-10-28 终验实测加）：卡的 shoulder 比批次宽时，归一会把
    p95 锚到 >100（实测 96.5→102.9），曲线把高光硬剪 ⇒ p99=100，违反
    all_p99≤97 硬门（大师 16 张 p99 全部 ≤96.9）。锚点一律 cap 到 97：
    肩形差的那点记 debt（规范 §7"不过门就记"），不硬造剪切。
    """
    keys = ('5', '25', '50', '75', '95')
    d_card = {k: gt[k] - gt['50'] for k in keys}
    d_batch = {k: xq[k] - xq['50'] for k in keys}
    return {k: min(xq[k] + d_card[k] - d_batch[k], cap) for k in keys}


def build_curve(x_lstar, y_lstar):
    """5 个锚点在 code 空间过点；端点锁；单调；限斜率。返回 16 点 (code_in, code_out)。"""
    xs = np.concatenate([[0.0], lstar_to_code(x_lstar), [255.0]])
    ys = np.concatenate([[0.0], lstar_to_code(y_lstar), [255.0]])
    o = np.argsort(xs)
    xs, ys = xs[o], ys[o]
    ux, uy = [], []
    for x, y in zip(xs, ys):
        if ux and abs(x - ux[-1]) < 1e-6:
            uy[-1] = y
        else:
            ux.append(x)
            uy.append(y)
    xs, ys = np.array(ux), np.array(uy)
    ys = np.maximum.accumulate(ys)                       # 单调
    # 限斜率：相邻点斜率不超过 SLOPE_MAX（在 0-255 的 x 上）
    for i in range(1, len(ys)):
        cap = ys[i - 1] + SLOPE_MAX * (xs[i] - xs[i - 1])
        ys[i] = min(ys[i], cap)
    ys = np.clip(ys, 0, 255)
    ys[-1] = 255.0
    grid = np.linspace(0, 255, 16)
    out = np.interp(grid, xs, ys)
    out[0], out[-1] = 0.0, 255.0
    return [(float(a), float(b)) for a, b in zip(grid, out)]


def curve_spec(pts):
    return 'ToneCurvePV2012:' + ','.join('%d,%d' % (round(a), round(b)) for a, b in pts)


# ---------- 批次 ----------
def measure_batch(paths):
    out = []
    for p in paths:
        m = measure(p)
        out.append(dict(path=p, stem=os.path.basename(p).replace('_svFilm_full.tif', ''),
                        valid=m.get('face_valid'), reliable=m.get('face_reliable'),
                        L=m.get('face_L_place'), Lg=(m.get('face') or {}).get('L'),
                        C=(m.get('face') or {}).get('C'), h=(m.get('face') or {}).get('h'),
                        gq=m.get('gq'), p99=m.get('all_p99')))
    return out


def run_go(card, paths, base_recs, ev, gt, L_lo, L_hi, T_L, out):
    """按规范跑一遍：一个 EV → 量 → 一条曲线 → 量 → 一组 HSL → 终验。

    ★ 曲线的锚点必须**曝光导出之后再量**（计划阶段那组是估计值：EV 一加高光会裁，
      分位不是简单平移）。这条是 ADR-002 的硬要求。
    """
    import numpy as _np
    files = {}
    print('\n== 1) 逐张应用批次 EV=%+.2f' % ev)
    for p in paths:
        f = LS.fresh(p, 'B_' + os.path.basename(p).replace('_svFilm_full.tif', ''))
        LS.zero(f)
        LS.apply(f, dict(LS.ZERO, Exposure2012=ev))
        files[p] = f
    after = []
    for p, f in files.items():
        m = measure(LS.expo(f, 'ev'))
        after.append(dict(path=p, L=m.get('face_L_place'), gq=m.get('gq'),
                          Lg=(m.get('face') or {}).get('L'), C=(m.get('face') or {}).get('C'),
                          h=(m.get('face') or {}).get('h'), p99=m.get('all_p99'),
                          valid=m.get('face_valid'), reliable=m.get('face_reliable')))
        print('   %-9s L=%6.2f  全图 p5/50/95 = %5.1f/%5.1f/%5.1f' % (
            os.path.basename(p)[:9], after[-1]['L'] or -1,
            after[-1]['gq']['5'], after[-1]['gq']['50'], after[-1]['gq']['95']))
    ok2 = [r for r in after if r['valid'] and r['gq']]
    xq = {k: float(_np.median([r['gq'][k] for r in ok2])) for k in ('5', '25', '50', '75', '95')}
    # ★ ADR-007：锚点必须先**形状归一**（p50 恒等，只搬形状）。
    #   实测教训（2026-10-28 终验）：直接拿原始卡锚点建曲线，p50 会把批次从 70.6
    #   硬拉到 56.3，脸被整体压 −14 L*、三张全部出带 —— 这正是 ADR-007 要防的。
    y_norm = normalize_anchors(xq, gt)
    pts = build_curve([xq[k] for k in ('5', '25', '50', '75', '95')],
                      [y_norm[k] for k in ('5', '25', '50', '75', '95')])
    print('== 2) 用**实测**锚点建曲线：批次 %s → 形状归一后 %s（原始卡锚 %s）' % (
        [round(xq[k], 1) for k in ('5', '25', '50', '75', '95')],
        [round(y_norm[k], 1) for k in ('5', '25', '50', '75', '95')],
        [round(gt[k], 1) for k in ('5', '25', '50', '75', '95')]))
    for f in files.values():
        LS.run('CURVES %s %s' % (f, curve_spec(pts)), timeout=180)
    after2 = []
    for p, f in files.items():
        m = measure(LS.expo(f, 'cv'))
        after2.append(dict(path=p, L=m.get('face_L_place'), C=(m.get('face') or {}).get('C'),
                           h=(m.get('face') or {}).get('h'), p99=m.get('all_p99'),
                           valid=m.get('face_valid'), reliable=m.get('face_reliable')))
    hs = [r['h'] for r in after2 if r['reliable'] and r['h'] is not None]
    cs = [r['C'] for r in after2 if r['reliable'] and r['C'] is not None]
    hue_o = sat_o = 0.0
    if hs and cs:
        dh = ((card['face']['skin']['h']['med'] - float(_np.median(hs)) + 180) % 360) - 180
        dC = card['face']['skin']['C']['med'] - float(_np.median(cs))
        hue_o = float(_np.clip(0.9 * dh / 0.30, -80, 80))     # 增益取保守值，跑完再核
        sat_o = float(_np.clip(0.9 * dC / 0.084, -60, 60))
        print('== 3) 一批一组 HSL：Δh=%+.1f ΔC=%+.1f ⇒ HueO=%+.1f SatO=%+.1f' % (dh, dC, hue_o, sat_o))
        for f in files.values():
            LS.apply(f, dict(LS.ZERO, Exposure2012=ev,
                             HueAdjustmentOrange=hue_o, SaturationAdjustmentOrange=sat_o))
    else:
        print('== 3) 可靠肤色样本不足 ⇒ 不解 HSL')
    print('== 4) 终验')
    recs = []
    for p, f in files.items():
        tif = LS.expo(f, 'fin')
        m = measure(tif)
        L = m.get('face_L_place')
        inb = bool(L is not None and L_lo <= L <= L_hi)
        recs.append(dict(stem=os.path.basename(p)[:9], L=L, Lg=(m.get('face') or {}).get('L'),
                         C=(m.get('face') or {}).get('C'), h=(m.get('face') or {}).get('h'),
                         p99=m.get('all_p99'), valid=m.get('face_valid'),
                         reliable=m.get('face_reliable'), in_band=inb))
        print('   %-9s L=%6.2f C=%5.2f h=%6.2f p99=%5.1f  %s%s' % (
            recs[-1]['stem'], L or -1, recs[-1]['C'] or -1, recs[-1]['h'] or -1,
            recs[-1]['p99'] or -1, '带内' if inb else 'outlier',
            '' if recs[-1]['valid'] else ' ★不可测'))
        Image.open(tif).convert('RGB').save(
            os.path.join(out, 'final_%s.jpg' % recs[-1]['stem']), quality=94)
    okn = sum(1 for r in recs if r['in_band'])
    print('   ⇒ 带内 %d/%d（规范要的行为：outlier 只标不拉）' % (okn, len(recs)))
    json.dump(dict(ev=ev, curve=pts, hue_o=hue_o, sat_o=sat_o, recs=recs),
              open(os.path.join(out, 'result.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1, default=float)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--card', type=int, default=38)
    ap.add_argument('--batch', default=os.path.join(BASE, 'look_first_919', 'engine_tif'))
    ap.add_argument('--out', default=None)
    ap.add_argument('--go', action='store_true', help='真的跑 LR；不给就只出计划')
    a = ap.parse_args()
    card = json.load(open(os.path.join(BASE, 'card_%d_target.json' % a.card), encoding='utf-8'))
    T_L = card['face']['skin']['L']['med']
    L_lo, L_hi = card['face']['L_lo'], card['face']['L_hi']
    gt = {k: card['gq'][k]['med'] for k in ('5', '25', '50', '75', '95')}

    paths = sorted(os.path.join(a.batch, f) for f in os.listdir(a.batch)
                   if f.endswith('_svFilm_full.tif'))
    recs = measure_batch(paths)
    ok = [r for r in recs if r['valid'] and r['L'] is not None]
    print('批次 %s：%d 张，可测 %d' % (os.path.basename(a.batch.rstrip('\\')), len(recs), len(ok)))
    if not ok:
        print('无可测样本'); return
    Ls = np.array([r['L'] for r in ok])
    disp = float(np.percentile(Ls, 75) - np.percentile(Ls, 25))
    print('   脸 L（落点）：%s' % np.round(Ls, 1))
    print('   批内 IQR = %.1f L*（门 %.1f）' % (disp, DISPERSION_MAX))
    if disp > DISPERSION_MAX:
        print('   ⇒ 【batch_too_dispersed】这批不是一批：先按情境拆，别解。')
        print('      （规范 ADR-001：批内散布 >1.5 个带宽时，一个 EV 覆盖不了；'
              '逐图解=违规，放宽 clamp=掩盖）')
        return
    # 一个 EV：亮度比
    ev = float(np.clip(np.log2(lstar_to_y(T_L) / lstar_to_y(np.median(Ls))), -EV_CLAMP, EV_CLAMP))
    L_after = np.array([float(lstar_to_code(r['L']) and 0) or 0 for r in ok])  # 占位，不参与
    print('   ⇒ 批次 EV = %+.2f（亮度比 log2）' % ev)
    # 曝光后的预计落点（用实测增益 17.05 L*/EV 粗估，真值跑完再说）
    pred = Ls + ev * 17.05
    inb = [bool(L_lo <= x <= L_hi) for x in pred]
    print('   预计带内 %d/%d；outlier: %s' % (
        sum(inb), len(ok), [ok[i]['stem'] for i, b in enumerate(inb) if not b]))

    # 曲线：批次（曝光后）全图分位 → 卡片全图分位。
    # ★ 这里是**计划阶段的估计**：真实锚点必须在 `--go` 里"曝光导出后再量"，
    #   因为 EV 一加，高光会裁、分位不是简单平移（实测 919 的 p95 会顶到 100）。
    xq = {}
    for k in ('5', '25', '50', '75', '95'):
        xq[k] = float(np.median([r['gq'][k] for r in ok if r.get('gq')]))
    xq_after = {k: float(np.clip(xq[k] + ev * 17.05, 0, 97.0)) for k in xq}
    y_norm = normalize_anchors(xq_after, gt)
    pts = build_curve([xq_after[k] for k in ('5', '25', '50', '75', '95')],
                      [y_norm[k] for k in ('5', '25', '50', '75', '95')])
    print('   曲线锚点 批次(曝光后·估计) %s → 形状归一后 %s' % (
        [round(xq_after[k], 1) for k in ('5', '25', '50', '75', '95')],
        [round(y_norm[k], 1) for k in ('5', '25', '50', '75', '95')]))
    print('   曲线 16 点: %s' % ','.join('%d→%d' % (a_, b_) for a_, b_ in pts[::3]))
    # 纯算术自检：曲线在"脸当前档"上是恒等还是被压回去（对比旧做法）
    face_now = float(np.median(Ls)) + ev * 17.05
    shift_norm = float(np.interp(face_now, [xq_after[k] for k in ('5', '25', '50', '75', '95')],
                                 [y_norm[k] for k in ('5', '25', '50', '75', '95')])) - face_now
    y_raw = {k: gt[k] for k in gt}
    shift_raw = float(np.interp(face_now, [xq_after[k] for k in ('5', '25', '50', '75', '95')],
                                [y_raw[k] for k in ('5', '25', '50', '75', '95')])) - face_now
    print('   曲线在脸档(%.1f)的位移：形状归一 %+.1f L* ｜ 旧做法(硬锚) %+.1f L*' % (
        face_now, shift_norm, shift_raw))

    # HSL：批次中位残差
    hs = [r['h'] for r in ok if r['reliable'] and r['h'] is not None]
    cs = [r['C'] for r in ok if r['reliable'] and r['C'] is not None]
    h_t, C_t = card['face']['skin']['h']['med'], card['face']['skin']['C']['med']
    if hs and cs:
        dh = ((h_t - float(np.median(hs)) + 180) % 360) - 180
        dC = C_t - float(np.median(cs))
        print('   肤色中位 h=%.1f C=%.1f（可靠样本 %d/%d）⇒ Δh=%+.1f ΔC=%+.1f' % (
            float(np.median(hs)), float(np.median(cs)), len(hs), len(ok), dh, dC))
    else:
        dh = dC = 0.0
        print('   肤色可靠样本不足 ⇒ 本批不解 HSL')

    plan = dict(card=a.card, batch=a.batch, dispersion=disp, ev=ev,
                curve=pts, d_h=dh, d_C=dC, recs=recs,
                pred_in_band=dict(zip([r['stem'] for r in ok], inb)))
    out = a.out or os.path.join(os.path.dirname(a.batch), 'solve_batch_card%d' % a.card)
    os.makedirs(out, exist_ok=True)
    json.dump(plan, open(os.path.join(out, 'plan.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1, default=float)
    print('\n计划已写 %s' % os.path.join(out, 'plan.json'))
    if not a.go:
        print('（未加 --go，不动 LR）')
        return
    run_go(card, paths, ok, ev, gt, L_lo, L_hi, T_L, out)


if __name__ == '__main__':
    main()
