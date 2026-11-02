# -*- coding: utf-8 -*-
"""标准色卡标定：ColorChecker Classic 24 + 21 级灰阶 → 引擎曝光表。

尺子只用色卡；图只提供“它自己有多亮”的输入亮度分布。
  · `render_table(medium)`：同一 medium/WB/1080，在 5 个 PE 档渲色卡，
    量每个色块中心 → `input_luminance × PE → output_L*` 表；
  · `solve_pe(lin, target)`：按这张图的输入亮度分布查表，解出把整幅 p50
    放到 target 的 PE；不渲图、不试。

表的结构（JSON）：
  {chart_sha, medium, pe_nominal, pes: [...],
   wedge: {ev: [...], y_linear: [...], L: [[... per PE] ...]},
   cc: {names: [...], Lab: [[[L,a,b] ...] per PE]}}
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional

import numpy as np

CHART_TIF = r'E:\ds\photo\_chart\colorchecker_classic_21step_linear_prophoto.tif'
CHART_JSON = r'E:\ds\photo\_chart\colorchecker_classic_21step_patches.json'
TABLE = r'E:\svStudio\svFilm\analysis\corpus\kurai\exposure_table.json'

# ProPhoto RGB (D50) → XYZ 的 Y 行
_Y_RGB = np.array([0.2880402, 0.7118741, 0.0000857], np.float64)


def _sha(path: str) -> str:
    h = hashlib.sha1()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()[:16]


def load_chart(tif_path: str = CHART_TIF, meta_path: str = CHART_JSON):
    """→ (linear ProPhoto HxWx3 float32, patch meta dict, chart_sha)."""
    import tifffile
    arr = tifffile.imread(tif_path)
    if arr.dtype == np.uint16:
        arr = arr.astype(np.float32) / 65535.0
    else:
        arr = arr.astype(np.float32)
        if arr.max() > 1.5:
            arr /= 255.0
    meta = json.load(open(meta_path, encoding='utf-8'))
    return arr, meta, _sha(tif_path)


def _resize_chart(arr: np.ndarray, meta: dict, long_side: int):
    """把色卡缩到交付长边；patch 中心按同一比例缩放。"""
    from PIL import Image
    h, w = arr.shape[:2]
    k = float(long_side) / max(h, w)
    if abs(k - 1.0) > 1e-6:
        im = Image.fromarray((np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8))
        im = im.resize((max(1, int(round(w * k))), max(1, int(round(h * k)))),
                       Image.LANCZOS)
        arr = np.asarray(im, np.float32) / 255.0
    if abs(k - 1.0) <= 1e-6:
        k = 1.0
    centers = []
    for p in (meta.get('colorchecker') or []) + (meta.get('wedge_patches') or []):
        c = p.get('center') or [0, 0]
        centers.append([int(round(c[0] * k)), int(round(c[1] * k))])
    return arr, centers, k


def _sample_lab(disp: np.ndarray, centers: List[List[int]], win: int = 5):
    """色块中心 win×win 的中位 Lab。"""
    from svFilm import color as _color
    lab = _color.to_lab(np.clip(np.asarray(disp, np.float64), 0.0, 1.0))
    out = []
    for cx, cy in centers:
        y0, y1 = max(0, cy - win), min(lab.shape[0], cy + win + 1)
        x0, x1 = max(0, cx - win), min(lab.shape[1], cx + win + 1)
        block = lab[y0:y1, x0:x1].reshape(-1, 3)
        out.append(np.median(block, axis=0))
    return np.asarray(out, np.float64)


def render_table(medium: dict, out_path: str = TABLE,
                 pe_values: Optional[List[float]] = None,
                 long_side: int = 1080, log=print) -> str:
    """渲 5 个 PE 档的色卡，量色块，写曝光表。"""
    from svFilm import presets
    medium = dict(medium)
    sim = dict(medium.get('simulation') or {})
    sim['auto_exposure'] = False
    medium['simulation'] = sim
    nominal = float(sim.get('print_exposure') or 0.987)
    if pe_values is None:
        pe_values = [round(nominal * (2.0 ** e), 6) for e in (-2, -1, 0, 1, 2)]
    arr, meta, sha = load_chart()
    chart, centers, _ = _resize_chart(arr, meta, long_side)
    n_cc = len(meta.get('colorchecker') or [])
    n_w = len(meta.get('wedge_patches') or [])
    Lmat, CC = [], []
    for pe in pe_values:
        log('  色卡 PE=%.4f（长边 %d）' % (pe, long_side))
        disp = presets.render(chart, medium, print_exposure=float(pe))
        lab = _sample_lab(disp, centers)
        Lmat.append(lab[n_cc:n_cc + n_w, 0].tolist())
        CC.append(lab[:n_cc, :].tolist())
    doc = dict(
        chart_sha=sha, chart_tif=os.path.abspath(CHART_TIF),
        medium=dict(stock=sim.get('film_stock'), paper=sim.get('print_paper')),
        pe_nominal=nominal, pes=[float(x) for x in pe_values],
        long_side=int(long_side),
        wedge=dict(ev=[p['ev'] for p in meta['wedge_patches']],
                   y_linear=[p['y_linear'] for p in meta['wedge_patches']],
                   L=Lmat),
        cc=dict(names=[p['name'] for p in meta['colorchecker']], Lab=CC),
        created=__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    )
    tmp = out_path + '.tmp'
    json.dump(doc, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    os.replace(tmp, out_path)
    log('  曝光表：%s（%d 个 PE 档）' % (out_path, len(pe_values)))
    return out_path


def load_table(path: str = TABLE) -> dict:
    return json.load(open(path, encoding='utf-8'))


def card_table_path(card_name: str) -> str:
    """Per-card PE table; never fall back to the old global table."""
    roots = []
    env = os.environ.get('SVFILM_PE_TABLES')
    if env:
        roots.append(env)
    sv = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots.append(os.path.join(sv, 'analysis', 'medium', 'out', 'pe_tables'))
    roots.append(os.path.join(sv, 'analysis', 'corpus', 'kurai', 'pe_tables'))
    for root in roots:
        p = os.path.join(root, card_name + '.json')
        if os.path.isfile(p):
            return p
    raise RuntimeError('缺少卡级 PE 表：%s（先跑 analysis/medium/pe_tables.py）' % card_name)


def load_card_table(card_name: str) -> dict:
    return load_table(card_table_path(card_name))


def _input_logy(lin: np.ndarray, max_px: int = 200000):
    arr = np.asarray(lin, np.float64)
    y = arr @ _Y_RGB
    flat = y.ravel()
    step = max(1, flat.size // max_px)
    flat = flat[::step]
    return np.log10(np.clip(flat, 1e-7, None))


def solve_pe(lin: np.ndarray, target: float, table: dict,
             pe_nominal: Optional[float] = None, span_ev: float = 4.0) -> dict:
    """按输入亮度分布解 PE；不渲图。返回 {pe, p50_pred, delta_ev, hit_bound}。"""
    pes = np.asarray(table['pes'], np.float64)
    order = np.argsort(pes)
    pes = pes[order]
    logy = np.log10(np.clip(np.asarray(table['wedge']['y_linear'], np.float64), 1e-7, None))
    oy = np.argsort(logy)
    logy = logy[oy]
    Lmat = np.asarray(table['wedge']['L'], np.float64)[order][:, oy]
    nom = float(pe_nominal if pe_nominal is not None else table.get('pe_nominal') or 1.0)
    x = _input_logy(lin)
    tgt = float(target)

    def p50_at(log2pe: float) -> float:
        # 在相邻两个 PE 档间做双线性：每个 PE 行按 logY 插值，再按 PE 插值
        l2 = np.log2(pes)
        if log2pe <= l2[0]:
            i0, i1, w = 0, 0, 0.0
        elif log2pe >= l2[-1]:
            i0, i1, w = len(l2) - 1, len(l2) - 1, 0.0
        else:
            i1 = int(np.searchsorted(l2, log2pe))
            i0 = i1 - 1
            w = (log2pe - l2[i0]) / (l2[i1] - l2[i0])
        L0 = np.interp(x, logy, Lmat[i0])
        if i0 == i1:
            vals = L0
        else:
            L1 = np.interp(x, logy, Lmat[i1])
            vals = (1.0 - w) * L0 + w * L1
        return float(np.median(vals))

    lo = np.log2(nom) - span_ev
    hi = np.log2(nom) + span_ev
    f_lo, f_hi = p50_at(lo), p50_at(hi)
    # PE 越大越暗（render_key 同口径）
    hit = False
    if tgt >= f_lo:
        best = lo
        hit = True
    elif tgt <= f_hi:
        best = hi
        hit = True
    else:
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            if p50_at(mid) > tgt:
                lo = mid
            else:
                hi = mid
        best = 0.5 * (lo + hi)
    pe = float(2.0 ** best)
    pred = p50_at(best)
    if abs(pred - tgt) > 0.5:
        hit = True
    return dict(pe=round(pe, 6), p50_pred=round(pred, 2),
                delta_ev=round(float(best - np.log2(nom)), 3),
                hit_bound=bool(hit), target=round(tgt, 2),
                pe_nominal=round(nom, 6), chart_sha=table.get('chart_sha'))
