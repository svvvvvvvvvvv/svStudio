# -*- coding: utf-8 -*-
"""筛第二维：光位之外，还有哪个量能把"卡内方差"解释得更多。

只读测量。做法：
  · 第一维固定 = 双区光位（back / mid / front / unknown）
  · 第二维候选 = 背景结构 / 主体占比 / 近场比 等，按三分位切
  · 判据 = 调整后 eta²（按格子数惩罚），且每格 n≥20（不足并入 other）
  · 同时看带有没有变窄（否则只是统计上好看）

跑：
  & $py -X utf8 -m design.dim_screen
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Callable, Dict, List, Optional

import numpy as np

MEASURE = r'E:\ds\photo\_run_1030_card004\m_鹿井_v4.json'

Q = {
    'face_L':       lambda r: (r.get('skin') or {}).get('L'),
    'face_over_bg': lambda r: ((r.get('skin') or {}).get('L') /
                               (r.get('bg') or {}).get('L_p90')
                               if (r.get('skin') or {}).get('L') and
                               ((r.get('bg') or {}).get('L_p90') or 0) >= 20 else None),
    'face_C':       lambda r: (r.get('skin') or {}).get('C'),
    'contrast':     lambda r: (((r.get('img') or {}).get('p95') or 0) - ((r.get('img') or {}).get('p5') or 0)
                               if (r.get('img') or {}).get('p95') is not None else None),
}


def label_dual(r: dict) -> str:
    sk = (r.get('skin') or {}).get('L')
    bg = (r.get('bg') or {}).get('L_p90')
    dk = r.get('face_dark_share40')
    if sk is None or not bg or dk is None:
        return 'unknown'
    gap = float(bg) - float(sk)
    if gap >= 35.0 or (dk >= 0.35 and gap >= 12.0):
        return 'back'
    if dk <= 0.12 and gap <= 12.0:
        return 'front'
    return 'mid'


# ---- 第二维候选（返回 float 或 None）
def c_bg_struct(r):
    b = r.get('bg') or {}
    if b.get('L_p90') is None or b.get('L') is None:
        return None
    return float(b['L_p90']) - float(b['L'])


def c_face_size(r):
    v = r.get('face_size')
    if v is None:
        v = r.get('face_mask_share')
    return None if v is None else float(v)


def c_near_ratio(r):
    nb = (r.get('near_bg') or {}).get('L_p90')
    bg = (r.get('bg') or {}).get('L_p90')
    if not nb or not bg:
        return None
    return float(nb) / float(bg)


def c_env(r):
    v = r.get('env_share')
    return None if v is None else float(v)


CAND = {
    '背景结构 bg90−bg50': c_bg_struct,
    '主体占比 face_size': c_face_size,
    '近场/背景 near90÷bg90': c_near_ratio,
    '环境占比 env_share': c_env,
}


def terciles(rows: List[dict], fn) -> Callable[[dict], str]:
    vs = [fn(r) for r in rows]
    vs = [v for v in vs if v is not None and np.isfinite(v)]
    q1, q2 = np.percentile(vs, [33.3, 66.7])
    def f(r):
        v = fn(r)
        if v is None or not np.isfinite(v):
            return 'na'
        return 'lo' if v <= q1 else ('mid' if v <= q2 else 'hi')
    return f


def eta2_adj(rows: List[dict], key: str, cell: Callable[[dict], str], min_n: int = 20):
    vals, cells = [], []
    for r in rows:
        v = Q[key](r)
        if v is None or not np.isfinite(v):
            continue
        vals.append(float(v)); cells.append(cell(r))
    if len(vals) < 50:
        return None, 0, 0
    cnt = Counter(cells)
    keep = {k for k, n in cnt.items() if n >= min_n and k != 'na'}
    if not keep:
        return None, 0, len(keep)
    cells = [c if c in keep else 'other' for c in cells]
    a = np.asarray(vals)
    gm = a.mean(); ss_t = float(((a - gm) ** 2).sum())
    ss_b = 0.0
    for k in set(cells):
        m = a[[c == k for c in cells]]
        if len(m) >= 2:
            ss_b += len(m) * (m.mean() - gm) ** 2
    if ss_t <= 0:
        return None, 0, len(keep)
    eta = ss_b / ss_t
    n = len(a); kk = len(set(cells))
    adj = 1.0 - (1.0 - eta) * (n - 1) / max(n - kk - 1, 1)
    return round(eta, 3), round(adj, 3), len(keep)


def main():
    d = json.load(open(MEASURE, encoding='utf-8'))
    rows = [r for r in (d.get('per_image') or []) if r.get('skin')]
    print('有脸 %d 张' % len(rows))
    print('\n光位（单因子基线）：')
    for k, v in sorted(Counter(label_dual(r) for r in rows).items()):
        print('  %-8s %4d' % (k, v))

    print('\n%-22s %-13s %6s %6s %5s %6s' % ('第二维', '量', 'eta²', '调整', '格数', 'n'))
    best: Dict[str, tuple] = {}
    for name, fn in CAND.items():
        bins = terciles(rows, fn)
        for key in Q:
            cell = lambda r, b=bins: '%s/%s' % (label_dual(r), b(r))
            eta, adj, nk = eta2_adj(rows, key, cell)
            if eta is None:
                continue
            print('%-22s %-13s %6.3f %6.3f %5d %6d' % (name, key, eta, adj, nk, len(rows)))
            if key == 'face_L' and (not best.get(name) or adj > best[name][1]):
                best[name] = (eta, adj, nk)
    print('\nface_L 上最好的第二维：')
    for name, (e, a, k) in sorted(best.items(), key=lambda kv: -kv[1][1]):
        print('  %-22s eta²=%.3f 调整=%.3f 格数=%d' % (name, e, a, k))


if __name__ == '__main__':
    main()
