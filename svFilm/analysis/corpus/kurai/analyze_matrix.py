# -*- coding: utf-8 -*-
"""把 `lr_matrix_1030.py` 的实测拟合成**响应曲线**，并与 borrowed 模型对拍。

输出：
    fit_coeffs.json   —— 每根滑杆的 k(L)=a+b·L、Saturation 的 r(S)=a+b·S、局部刻度
    fit_report.md     —— 残差表 + 结论（哪些成立、哪些否掉）

原则：
· 只做**一阶**响应（单变量、零基线）；交互不在本矩阵里；
· 拟合的是"每 100 单位滑杆 → 多少 L*"（k 的单位 L*/100），不是"每点多少"；
· 与 `tone_model`（autoshade 结构）对比：如果它的残差显著更大，就明确否掉它。
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from typing import Dict, List

import numpy as np

sys.path.insert(0, r'E:\svStudio\svFilm')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WORK = r'E:\ds\photo\_lr_matrix_1030'
Q = ('p5', 'p25', 'p50', 'p75', 'p95')


def _base(rows, tag):
    for r in rows:
        if r.get('tag') == tag:
            return r
    return None


def _linfit(x, y):
    """最小二乘 a+b·x；返回 (a,b,n,rms)。"""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if len(x) < 2 or np.ptp(x) < 1e-9:
        return (float(np.mean(y)) if len(y) else 0.0), 0.0, len(x), 0.0
    A = np.vstack([np.ones_like(x), x]).T
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    pred = A @ coef
    rms = float(np.sqrt(np.mean((pred - y) ** 2)))
    return float(coef[0]), float(coef[1]), len(x), rms


def tone(rows):
    out = {}
    sliders = ('contrast', 'highlights', 'shadows', 'whites', 'blacks')
    for s in sliders:
        raw = []            # (source, L, val, delta)
        for r in rows:
            if r.get('group') != 'tone' or s not in (r.get('settings') or {}):
                continue
            b = r.get('base') or {}
            if not b:
                continue
            val = float(r['settings'][s]) / 100.0       # ±0.5
            for q in Q:
                L0 = (b.get('img') or {}).get(q)
                L1 = (r.get('img') or {}).get(q)
                if L0 is None or L1 is None:
                    continue
                raw.append((r.get('source'), float(L0), val, float(L1) - float(L0)))
        if not raw:
            continue
        pts = [(L, d / v) for _, L, v, d in raw]
        a, b, n, rms = _linfit([p[0] for p in pts], [p[1] for p in pts])
        # 同数据上的实际残差（L*）+ 留一源交叉验证
        errs = [abs(d - (a + b * L) * v) for _, L, v, d in raw]
        cv = []
        for src in sorted({x[0] for x in raw}):
            tr = [(L, d / v) for s0, L, v, d in raw if s0 != src]
            te = [(L, v, d) for s0, L, v, d in raw if s0 == src]
            if len(tr) < 4 or not te:
                continue
            aa, bb, _, _ = _linfit([p[0] for p in tr], [p[1] for p in tr])
            cv += [abs(d - (aa + bb * L) * v) for L, v, d in te]
        out[s] = dict(a=round(a, 4), b=round(b, 5), n=n, rms=round(rms, 3),
                      mae=round(st.mean(errs), 2),
                      p90=round(float(np.percentile(errs, 90)), 2),
                      max=round(max(errs), 2),
                      cv_mae=round(st.mean(cv), 2) if cv else None,
                      cv_p90=round(float(np.percentile(cv, 90)), 2) if cv else None,
                      slope_at=dict(p25=round(a + b * 25, 2),
                                    p50=round(a + b * 50, 2),
                                    p75=round(a + b * 75, 2)))
    return out


def sat(rows):
    """ΔC = C0 · (a·S + b·S²) / 100（S 为 UI）。"""
    pts = []
    for r in rows:
        if r.get('group') != 'sat':
            continue
        b = r.get('base') or {}
        C0 = b.get('face_C')
        S = float((r.get('settings') or {}).get('saturation') or 0)
        if C0 is None or S == 0 or r.get('face_C') is None:
            continue
        pts.append((S, (float(r['face_C']) - float(C0)) / (float(C0) * S / 100.0)))
    if not pts:
        return {}
    # r(S) = a + b·S
    a, b, n, rms = _linfit([p[0] for p in pts], [p[1] for p in pts])
    return dict(a=round(a, 5), b=round(b, 6), n=n, rms=round(rms, 4),
                model='dC = C0 * (a*S + b*S*S)/100',
                at30=round(a + b * 30, 4), at60=round(a + b * 60, 4))


def exposure(rows):
    from design.photometry import l_after_ev
    res = {}
    for sign in (-0.5, 0.5):
        errs = []
        for r in rows:
            if r.get('group') != 'exp':
                continue
            if abs(float((r.get('settings') or {}).get('exposure') or 0) - sign) > 1e-9:
                continue
            b = r.get('base') or {}
            for q in ('p50', 'p95', 'face_L'):
                L0 = (b.get('img') or {}).get(q) if q != 'face_L' else b.get('face_L')
                L1 = (r.get('img') or {}).get(q) if q != 'face_L' else r.get('face_L')
                if L0 is None or L1 is None:
                    continue
                errs.append(float(L1) - l_after_ev(float(L0), sign))
        if errs:
            res['ev%+.1f' % sign] = dict(n=len(errs), mean=round(st.mean(errs), 2),
                                         mae=round(st.mean(abs(e) for e in errs), 2),
                                         max=round(max(abs(e) for e in errs), 2))
    return res


def local(rows):
    out = {}
    le = [r for r in rows if r.get('group') == 'local' and 'LocalExposure2012' in (r.get('settings') or {})]
    if le:
        pts = []
        for r in le:
            b = r.get('base') or {}
            if b.get('face_L') is None or r.get('face_L') is None:
                continue
            ev = float(r['settings']['LocalExposure2012']) * 4.0
            pts.append((ev, (float(r['face_L']) - float(b['face_L'])) / ev))
        if pts:
            a, bb, n, rms = _linfit([p[0] for p in pts], [p[1] for p in pts])
            out['local_exposure'] = dict(eff=a, slope=bb, n=n, rms=round(rms, 2))
    ls = [r for r in rows if r.get('group') == 'local' and 'LocalShadows2012' in (r.get('settings') or {})]
    if ls:
        vals = []
        for r in ls:
            b = r.get('base') or {}
            if b.get('face_L') is None or r.get('face_L') is None:
                continue
            vals.append((float(r['settings']['LocalShadows2012']) * 100.0,
                         float(r['face_L']) - float(b['face_L'])))
        out['local_shadows'] = dict(n=len(vals),
                                    pairs=[(round(x, 1), round(y, 2)) for x, y in vals])
    lsa = [r for r in rows if r.get('group') == 'local' and 'LocalSaturation' in (r.get('settings') or {})]
    if lsa:
        pts = []
        for r in lsa:
            b = r.get('base') or {}
            if b.get('face_C') is None or r.get('face_C') is None:
                continue
            u = float(r['settings']['LocalSaturation']) * 100.0
            pts.append((u / 100.0, (float(r['face_C']) - float(b['face_C']))
                        / (float(b['face_C']) * u / 100.0)))
        a, bb, n, rms = _linfit([p[0] for p in pts], [p[1] for p in pts]) if pts else (0, 0, 0, 0)
        out['local_saturation'] = dict(r=round(a, 4), n=n, rms=round(rms, 3))
    ton = [r for r in rows if r.get('group') == 'local' and 'LocalToningSaturation' in (r.get('settings') or {})]
    if ton:
        out['local_toning'] = [dict(hue=r['settings'].get('LocalToningHue'),
                                    face_C=round(r.get('face_C') or 0, 1),
                                    face_h=round(r.get('face_h') or 0, 1)) for r in ton]
    return out


def borrowed(rows):
    """对比：autoshade tone_model 在同一批臂上的残差（L*）。"""
    from _lr_models.tone_model import predict_l
    errs = []
    for r in rows:
        if r.get('group') != 'tone':
            continue
        b = r.get('base') or {}
        s = r.get('settings') or {}
        key = next(iter(s))
        val = float(s[key])
        for q in Q:
            L0 = (b.get('img') or {}).get(q)
            L1 = (r.get('img') or {}).get(q)
            if L0 is None or L1 is None:
                continue
            errs.append(float(L1) - predict_l(float(L0), {key: val}))
    if not errs:
        return {}
    return dict(n=len(errs), mean_abs=round(st.mean(abs(e) for e in errs), 2),
                p90_abs=round(float(np.percentile(np.abs(errs), 90)), 2),
                max_abs=round(max(abs(e) for e in errs), 2))


def main():
    rows = json.load(open(os.path.join(WORK, 'matrix.json'), encoding='utf-8'))['rows']
    rows = [r for r in rows if not r.get('error')]
    coeff = dict(n_arms=len(rows), tone=tone(rows), sat=sat(rows),
                 exposure=exposure(rows), local=local(rows),
                 borrowed_tone=borrowed(rows))
    json.dump(coeff, open(os.path.join(WORK, 'fit_coeffs.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    lines = ['# LR 矩阵拟合（2026-10-31）', '',
             '> 数据：`matrix.json`（%d 臂，单变量、零基线）。' % len(rows), '',
             '## 1 全局滑杆：k(L)=a+b·L，单位 L*/100 滑杆值', '',
             '| 滑杆 | a | b | n | MAE | P90 | CV MAE | CV P90 | k(25) | k(50) | k(75) |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for k, v in coeff['tone'].items():
        s = v['slope_at']
        lines.append('| %s | %.3f | %.5f | %d | %.2f | %.2f | %s | %s | %.2f | %.2f | %.2f |'
                     % (k, v['a'], v['b'], v['n'], v['mae'], v['p90'],
                        v.get('cv_mae', '—'), v.get('cv_p90', '—'),
                        s['p25'], s['p50'], s['p75']))
    lines += ['', '## 2 Saturation：ΔC = C0·(a·S + b·S²)/100', '',
              '```', json.dumps(coeff['sat'], ensure_ascii=False), '```', '',
              '## 3 曝光 / 局部', '',
              '```', json.dumps(dict(exposure=coeff['exposure'], local=coeff['local']),
                               ensure_ascii=False, indent=1), '```', '',
              '## 4 与 borrowed tone_model 的对比', '',
              '```', json.dumps(coeff['borrowed_tone'], ensure_ascii=False), '```', '']
    open(os.path.join(WORK, 'fit_report.md'), 'w', encoding='utf-8').write('\n'.join(lines))
    print(json.dumps(coeff, ensure_ascii=False, indent=1)[:4000])
    print('写出', WORK)


if __name__ == '__main__':
    main()
