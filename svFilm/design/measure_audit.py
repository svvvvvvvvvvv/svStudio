# -*- coding: utf-8 -*-
"""测量数据的验收（不是图像的验收）。设计真源 §62.4。

**为什么要有这个**：求解器的每一个数都从测量来。数据哪里不对，解出来的就是垃圾——
而且不会报错，只会"看起来能跑"。所以拿数据去解之前，先让数据过一遍这几关。

★ 2026-10-29 修正（SV 指出）：肤色早已从"圆盘中位"改成 **圆里取一个点并证明它是皮肤**
  （`skin_point.py` v6：9×9 局部平整 + 多尺度一致 + 代表性 + 合理性 + 不许是最亮/最暗）。
  所以可信度**不看 `skin.n`**（那是旧圆口径的字段），要看新口径的四条证明：
  `skin.mad` · `skin.dE_scale` · `skin.dE_rep` · `skin.w`

用法：
    & $py -X utf8 design/measure_audit.py E:\\ds\\photo\\_corpus\\kurai\\full\\m_鹿井_v2.json ...
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from typing import Dict, List, Sequence

# ---- 门槛：**全部抄自 `skin_point.py` 的常量**（不许在这里另拍一套）
MAD_MAX = 6.0            # ① 9×9 窗内 a/b 两通道 std 之和
SCALE_DE_MAX = 4.0       # ② 0.06/0.10/0.14 脸宽三个尺度之间的 ΔE
REP_DE_MAX = 8.0         # ③ 与圆内中位色的 ΔE
FACE_W_MIN_1080 = 40.0   # ④ 脸太小就别测（1080 域像素）

# ---- 套级门槛（工程初值，不是物理；写在这里给人改）
MIN_SET_N = 5
MAX_SET_P50_IQR = 15.0
SKY_CONTAMINATED = 0.30


def _rows(d) -> List[dict]:
    if isinstance(d, dict):
        return list(d.get('per_image') or [])
    return list(d or [])


def _set_of(row: dict) -> str:
    p = row.get('file') or row.get('path') or ''
    return os.path.basename(os.path.dirname(p)) or '?'


def _iqr(xs: Sequence[float]) -> float:
    xs = sorted(x for x in xs if x is not None)
    if len(xs) < 2:
        return 0.0
    n = len(xs)
    return float(xs[int(0.75 * (n - 1))] - xs[int(0.25 * (n - 1))])


def audit_one(path: str) -> dict:
    d = json.load(open(path, encoding='utf-8'))
    rows = _rows(d)
    meta = (d.get('_meta') if isinstance(d, dict) else {}) or {}
    out = dict(path=path, name=os.path.basename(path), n=len(rows),
               caliber=meta.get('caliber') or meta.get('sha') or '',
               issues=[], sets=[])
    if not rows:
        out['issues'].append(('空数据', '一行都没有'))
        return out

    miss = [k for k in ('img', 'skin', 'bg', 'shape') if not rows[0].get(k)]
    if miss:
        out['issues'].append(('字段缺失', '缺 %s ⇒ 求解器读不到' % ','.join(miss)))

    cal = {json.dumps(r.get('caliber'), sort_keys=True, ensure_ascii=False)
           for r in rows if r.get('caliber') is not None}
    if len(cal) > 1:
        out['issues'].append(('口径不一致', '同一文件里 %d 种 caliber ⇒ 混版了' % len(cal)))

    # --- 关 3：按**点口径**判逐图可信度
    sk = [r for r in rows if (r.get('skin') or {}).get('L') is not None]
    out['n_with_skin'] = len(sk)
    bad_mad = [r for r in sk if (r['skin'].get('mad') or 0) > MAD_MAX]
    bad_scale = [r for r in sk if (r['skin'].get('dE_scale') or 0) > SCALE_DE_MAX]
    bad_rep = [r for r in sk if (r['skin'].get('dE_rep') or 0) > REP_DE_MAX]
    small_face = [r for r in sk if (r.get('face_w_native') or 0) < FACE_W_MIN_1080]
    no_guard = [r for r in sk if (r['skin'].get('mad') is None
                                  and r['skin'].get('dE_scale') is None
                                  and r['skin'].get('dE_rep') is None)]
    out.update(pct_bad_mad=100.0 * len(bad_mad) / max(len(sk), 1),
               pct_bad_scale=100.0 * len(bad_scale) / max(len(sk), 1),
               pct_bad_rep=100.0 * len(bad_rep) / max(len(sk), 1))
    if no_guard:
        out['issues'].append(('口径字段缺失',
                              '%d/%d 张没有 mad/dE_scale/dE_rep ⇒ 这份是**旧圆口径**测的，'
                              '不能按点口径判可信度' % (len(no_guard), len(sk))))
    for lab, arr, lim in (('局部不平整', bad_mad, MAD_MAX),
                          ('多尺度不一致', bad_scale, SCALE_DE_MAX),
                          ('不代表这块脸', bad_rep, REP_DE_MAX)):
        if arr:
            out['issues'].append((lab, '%d 张超限（门 %.1f）⇒ 肤色只能当线索' % (len(arr), lim)))
    if small_face:
        out['issues'].append(('脸太小', '%d 张 face_w < %.0f ⇒ 关键点几何不可靠'
                              % (len(small_face), FACE_W_MIN_1080)))

    # --- 关 4：p95 被天空顶住
    sky = [r.get('sky_share_frac') for r in rows if r.get('sky_share_frac') is not None]
    n_sky = len([s for s in sky if s >= SKY_CONTAMINATED])
    out['pct_sky'] = 100.0 * n_sky / max(len(sky), 1)
    if n_sky:
        out['issues'].append(('p95 被天空污染',
                              '%d 张天空 ≥ %.0f%% ⇒ 统计 p95 要标出或剔除'
                              % (n_sky, 100 * SKY_CONTAMINATED)))

    # --- 关 5：逐套
    by: Dict[str, List[dict]] = {}
    for r in rows:
        by.setdefault(_set_of(r), []).append(r)
    for name, rs in sorted(by.items()):
        p50 = [(r.get('img') or {}).get('p50') for r in rs if (r.get('img') or {}).get('p50') is not None]
        p95 = [(r.get('img') or {}).get('p95') for r in rs if (r.get('img') or {}).get('p95') is not None]
        face = [(r.get('skin') or {}).get('L') for r in rs if (r.get('skin') or {}).get('L') is not None]
        iqr50 = _iqr(p50)
        bad = []
        if len(rs) < MIN_SET_N:
            bad.append('n=%d < %d ⇒ 分位带不可靠' % (len(rs), MIN_SET_N))
        if iqr50 > MAX_SET_P50_IQR:
            bad.append('套内 p50 IQR %.0f > %.0f ⇒ 这套混了两种场景' % (iqr50, MAX_SET_P50_IQR))
        out['sets'].append(dict(name=name, n=len(rs), n_skin=len(face),
                                p50=st.median(p50) if p50 else None,
                                p95=st.median(p95) if p95 else None,
                                p50_iqr=iqr50,
                                faceL=st.median(face) if face else None, bad=bad))
    n_bad = len([s for s in out['sets'] if s['bad']])
    if n_bad:
        out['issues'].append(('套不合格', '%d/%d 套不能直接当卡用' % (n_bad, len(out['sets']))))
    return out


def main(argv: List[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    for r in (audit_one(p) for p in argv):
        print('=' * 78)
        print('%s   行数 %d ｜ 有肤色 %d' % (r['name'], r['n'], r.get('n_with_skin', 0)))
        if r['caliber']:
            print('口径 %s' % (r['caliber'] if isinstance(r['caliber'], str)
                              else json.dumps(r['caliber'], ensure_ascii=False)[:80]))
        if r.get('pct_bad_mad') is not None:
            print('点口径四条证明超限率：局部 %.0f%% ｜ 多尺度 %.0f%% ｜ 代表性 %.0f%%'
                  % (r['pct_bad_mad'], r['pct_bad_scale'], r['pct_bad_rep']))
        if r['issues']:
            print('— 问题 —')
            for k, why in r['issues']:
                print('  ✗ %-12s %s' % (k, why))
        else:
            print('  ✓ 没有硬性问题')
        bad = [s for s in r['sets'] if s['bad']]
        if bad:
            print('— 不合格的套（前 8） —')
            for s in bad[:8]:
                print('  %-30s n=%-3d ｜ %s' % (s['name'][:30], s['n'], '；'.join(s['bad'])))
        print('— 套的分布（前 6） —')
        for s in sorted(r['sets'], key=lambda x: -(x['n'] or 0))[:6]:
            print('  %-30s n=%-3d p50=%5s IQR=%4.1f p95=%5s 脸L=%5s'
                  % (s['name'][:30], s['n'],
                     '—' if s['p50'] is None else '%.1f' % s['p50'], s['p50_iqr'],
                     '—' if s['p95'] is None else '%.1f' % s['p95'],
                     '—' if s['faceL'] is None else '%.1f' % s['faceL']))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
