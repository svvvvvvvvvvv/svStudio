# -*- coding: utf-8 -*-
"""标准色卡标定：ColorChecker Classic 24 + 灰阶 → 引擎曝光表。

尺子只用色卡；图只提供“它自己有多亮”的输入亮度分布。
  · `render_table(medium)`：同一 medium/WB/1080，在 5 个 PE 档渲色卡 + **合成灰阶条**，
    量每个色块/条中心 → `input_luminance × PE → output_L*` 表；
  · `solve_pe(lin, target)`：按这张图的输入亮度分布查表，解出把整幅 p50
    放到 target 的 PE；不渲图、不试。

★ 灰阶为什么不用印出来的楔子（2026-11-03 实测）：
  21 级楔子只到 −3.6 EV，y 下限 0.0148。人像里这个亮度以下的像素常常过半，
  `np.interp` 会把它钳在**最暗那一块**的输出上 ⇒ `p50_pred` 变成"表的暗端"、
  与图像无关（实测：7 张不同直方图解出**同一个 PE=0.347396**，渲出来比 target
  暗 17~21 L*，见 `_run_1031_rel` 复核）。
  解法不是外推（互易性不成立：同一条 L 在 (y, PE) 与 (2y, PE/2) 上差到 18 L*），
  而是**把暗端量出来**：自己造一条绝对 log2(y) −0.07~−11.75 的灰阶（`gray_ramp`），
  逐 PE 渲一遍 → 映射在整个输入范围内都是实测值。
  实测闭环（DSCF4332，target 33.3）：旧表解到 PE 0.3474 → 渲出来 p50 **12.2**；
  换合成条（+ PE 方向 PCHIP）解到 0.2041 → 渲出来 **33.7**（|Δ| 0.4）。

表的结构（JSON）：
  {chart_sha, medium, pe_nominal, pes: [...], wedge_mode: 'ramp'|'chart',
   wedge: {ev: [...], y_linear: [...], L: [[... per PE] ...]},
   cc: {names: [...], Lab: [[[L,a,b] ...] per PE]}}
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional

import numpy as np

from .curve_design import _pchip

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


# ---------------------------------------------------------------- 合成灰阶

RAMP_TOP = 0.95          # 最亮一条的线性值（与色卡最亮块同量级）
RAMP_STEP_EV = 1.0 / 3.0  # 每条之间 1/3 档
RAMP_N = 36              # 36 条 × 1/3 档：绝对 log2(y) 从 −0.07 到 −11.75（≈12 档）
RAMP_GREY = 0.18         # ev 的零点（18% 灰），与楔子的 ev 口径一致


def gray_ramp(n: int = RAMP_N, top: float = RAMP_TOP,
              step_ev: float = RAMP_STEP_EV, long_side: int = 1080):
    """合成灰阶条 → (ys, img, centers)。

    为什么不用印出来的楔子：楔子只到 −3.6 EV（y=0.0148），而人像里半数像素常在
    它之下 —— 查表时会被钳在最暗那块的输出上，`p50_pred` 就变成"表的暗端"、
    与图无关。自己造条就能把暗端**量出来**（实测把 17~21 L* 的偏差降到 ≤1 L*）。

    条是纯水平带：每条占 long_side/n 行，中心取样；相邻条只差 1/3 档，
    串光（halation/glare）比色卡楔子里"黑块贴亮块"小得多。
    """
    n = max(2, int(n))
    bar = max(12, int(long_side) // n)     # ★ 每条至少 12 行：取样窗 11 行才不会串到隔壁
    h = bar * n
    ys = [float(top) * (2.0 ** (-i * float(step_ev))) for i in range(n)]
    img = np.zeros((h, long_side, 3), np.float32)
    centers = []
    for i, y in enumerate(ys):
        img[i * bar:(i + 1) * bar, :, :] = float(y)
        centers.append([int(long_side // 2), int(i * bar + bar // 2)])
    return ys, img, centers


def _ramp_ev(ys: List[float]) -> List[float]:
    return [float(np.log2(max(float(y), 1e-9) / RAMP_GREY)) for y in ys]


def render_table(medium: dict, out_path: str = TABLE,
                 pe_values: Optional[List[float]] = None,
                 long_side: int = 1080, wedge_mode: str = 'ramp',
                 log=print) -> str:
    """渲每个 PE 档的色卡（+ 合成灰阶条），量色块/条，写曝光表。

    `wedge_mode`：'ramp'（默认，合成灰阶条，覆盖 −12 EV）｜'chart'（旧：21 级楔子）。
    """
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
    # ★ 灰阶来源（2026-11-03）：默认用**合成灰阶条**（覆盖 −0.07~−12 EV），
    #   'chart' 保留旧口径（印出来的 21 级楔子，只到 −3.6 EV）。
    ramp_ys, ramp_img, ramp_centers = gray_ramp(long_side=long_side)
    use_ramp = str(wedge_mode) == 'ramp'
    for pe in pe_values:
        log('  色卡 PE=%.4f（长边 %d%s）'
            % (pe, long_side, '，灰阶=合成条' if use_ramp else '，灰阶=印出的楔子'))
        disp = presets.render(chart, medium, print_exposure=float(pe))
        lab = _sample_lab(disp, centers)
        CC.append(lab[:n_cc, :].tolist())
        if use_ramp:
            rdisp = presets.render(ramp_img, medium, print_exposure=float(pe))
            bar = max(12, int(ramp_img.shape[0]) // len(ramp_ys))
            rlab = _sample_lab(rdisp, ramp_centers, win=max(1, min(5, bar // 3)))
            Lmat.append([float(v) for v in rlab[:, 0]])
        else:
            Lmat.append(lab[n_cc:n_cc + n_w, 0].tolist())
    if use_ramp:
        wedge = dict(ev=_ramp_ev(ramp_ys), y_linear=ramp_ys, L=Lmat)
    else:
        wedge = dict(ev=[p['ev'] for p in meta['wedge_patches']],
                     y_linear=[p['y_linear'] for p in meta['wedge_patches']],
                     L=Lmat)
    doc = dict(
        chart_sha=sha, chart_tif=os.path.abspath(CHART_TIF),
        medium=dict(stock=sim.get('film_stock'), paper=sim.get('print_paper')),
        pe_nominal=nominal, pes=[float(x) for x in pe_values],
        long_side=int(long_side), wedge_mode=('ramp' if use_ramp else 'chart'),
        wedge=wedge,
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


def _maps(table: dict):
    """表 → (pes, log2pes, logy, L)。对齐 y 与 PE 两轴，后续只用这几样。"""
    pes = np.asarray(table['pes'], np.float64)
    order = np.argsort(pes)
    pes = pes[order]
    y = np.asarray(table['wedge']['y_linear'], np.float64)
    logy = np.log10(np.clip(y, 1e-7, None))
    oy = np.argsort(logy)
    logy = logy[oy]
    Lmat = np.asarray(table['wedge']['L'], np.float64)[order][:, oy]
    return pes, np.log2(pes), logy, Lmat


def _row_at(Lmat: np.ndarray, l2: np.ndarray, log2pe: float) -> np.ndarray:
    """某个 PE 下的整条 y→L：PE 方向走**单调三次**，不是两点线性。

    暗端的 L(log2 PE) 曲得厉害（实测一条灰阶：55→20→4→1，每档跌幅 −35/−16/−3），
    线性插值会系统性偏亮：540 长边、PE=0.347 处线性 +2.9 L*，PCHIP +0.6 L*。
    表档数 ≥4 时用 PCHIP（Fritsch–Carlson，不越界、不反折），否则退线性。
    """
    if len(l2) >= 4:
        return np.array([_pchip(l2.tolist(), Lmat[:, j].tolist(), float(log2pe))
                         for j in range(Lmat.shape[1])], np.float64)
    if log2pe <= l2[0]:
        return Lmat[0]
    if log2pe >= l2[-1]:
        return Lmat[-1]
    i1 = int(np.searchsorted(l2, log2pe))
    i0 = i1 - 1
    w = (log2pe - l2[i0]) / (l2[i1] - l2[i0])
    return (1.0 - w) * Lmat[i0] + w * Lmat[i1]


Q_KEYS = (5, 25, 50, 75, 95)

# 五根分位的**自然离散**（746 张大师片，p10–p90 跨度 / 2.56 ≈ SD，L*）。
# 用途：直方图距离按它归一化 —— 两端天生窄（p5 只有 3.8），中位天生宽（17.5），
# 所以"暗部被抬 20 级"比"中位差 20 级"严重得多，权重必须不一样。
Q_SD = {5: 3.8, 25: 14.0, 50: 17.5, 75: 10.7, 95: 3.8}
Q_W = {k: 1.0 / v for k, v in Q_SD.items()}
FACE_TARGET_L = 70.6    # 大师片 skin.L 中位（746 帧）
FACE_BIAS = 5.0         # 「按脸框从 RAW 预测」实测偏低 5 L*（偏差补偿）


def _pred_q(x: np.ndarray, logy: np.ndarray, row: np.ndarray,
            keys=Q_KEYS) -> dict:
    vals = np.interp(x, logy, row)
    return {k: float(np.percentile(vals, k)) for k in keys}


def _model_check(x: np.ndarray, logy: np.ndarray, row: np.ndarray) -> dict:
    below = float(np.mean(x < logy[0]))
    above = float(np.mean(x > logy[-1]))
    ok = below < 0.5 and above < 0.5
    why = ''
    if below >= 0.5:
        ok = False
        why = ('暗端钳位：%.0f%% 的像素比灰阶最暗一条还黑 ⇒ 预测值只是表的暗端'
               % (100.0 * below))
    elif above >= 0.5:
        ok = False
        why = ('亮端钳位：%.0f%% 的像素比灰阶最亮一条还亮 ⇒ 预测值只是表的亮端'
               % (100.0 * above))
    return dict(model_ok=bool(ok), model_why=why,
                below_frac=round(below, 4), above_frac=round(above, 4),
                floor_L=round(float(row[0]), 2), ceil_L=round(float(row[-1]), 2))


def solve_pe_q(lin: np.ndarray, target_q: dict, table: dict,
               pe_nominal: Optional[float] = None, span_ev: float = 4.0) -> dict:
    """**直方图对齐**：解 PE，让这张渲出来的分位（p5/25/50/75/95）贴近 peer 的分位。

    这是设计里「找到 peer 后反向算 PE，让 RAW 的直方图跟 peer 大致对齐」那条，
    不再先压成一个 p50 再解（压成单数会丢掉反差信息：同样 p50=45 的高反差和
    低反差帧，要的 PE 不一样）。

    目标缺失的分位直接跳过；**锚中位**（一个自由度对不齐五个分位），
    其余分位在 q_pred / q_target 里照实报，差多少由 LR 的曲线补。
    """
    pes, l2, logy, Lmat = _maps(table)
    nom = float(pe_nominal if pe_nominal is not None else table.get('pe_nominal') or 1.0)
    x = _input_logy(lin)
    # 目标分位的键两种写法都收：50 / 'p50'（测量记录与 JSON 里都是 'p50'）
    tgt = {}
    for k, v in (target_q or {}).items():
        if not isinstance(v, (int, float)):
            continue
        kk = k
        if isinstance(k, str):
            kk = int(k[1:]) if k[:1] in ('p', 'P') and k[1:].isdigit() else (
                int(k) if k.isdigit() else None)
        if kk in Q_KEYS:
            tgt[int(kk)] = float(v)
    if not tgt:
        raise ValueError('solve_pe_q：没有可用的目标分位')

    # PE 只有**一个自由度**：五个分位不可能同时对上（film 的 toe 不会因为抬高
    # 中位就跟着变深 —— 实测同 PE 下影子差 15~20 L*）。所以先锚**中位**（对齐
    # 的直觉就是中位对齐），其余分位照实报，交 LR 的曲线去补形状。
    anchor = 50 if 50 in tgt else min(tgt, key=lambda k: abs(k - 50))

    def score(log2pe: float):
        row = _row_at(Lmat, l2, log2pe)
        q = _pred_q(x, logy, row, tuple(tgt))
        d = (q[anchor] - tgt[anchor]) ** 2
        return d, q, row

    lo = max(np.log2(nom) - span_ev, l2[0])
    hi = min(np.log2(nom) + span_ev, l2[-1])
    # 先粗扫（目标函数不一定单调：分位整体平移但形状会变），再在最优点附近细找
    grid = np.linspace(lo, hi, 41)
    best_l2, best = float(grid[0]), None
    for g in grid:
        d, q, row = score(float(g))
        if best is None or d < best[0]:
            best_l2, best = float(g), (d, q, row)
    step = (hi - lo) / 40.0
    for _ in range(30):
        cands = [best_l2 - step, best_l2 + step]
        improved = False
        for c in cands:
            c = max(lo, min(hi, c))
            d, q, row = score(c)
            if d < best[0] - 1e-9:
                best_l2, best, improved = c, (d, q, row), True
        if not improved:
            step *= 0.5
            if step < 1e-4:
                break
    d, q, row = best
    chk = _model_check(x, logy, row)
    pe = float(2.0 ** best_l2)
    return dict(pe=round(pe, 6), p50_pred=round(q.get(50, float('nan')), 2),
                q_pred={k: round(v, 1) for k, v in q.items()},
                q_target={k: round(v, 1) for k, v in tgt.items()},
                dist=round(float(np.sqrt(max(d, 0.0))), 3),
                delta_ev=round(float(best_l2 - np.log2(nom)), 3),
                hit_bound=bool(best_l2 <= lo + 1e-6 or best_l2 >= hi - 1e-6),
                target=round(tgt.get(50, float('nan')), 2),
                pe_nominal=round(nom, 6), chart_sha=table.get('chart_sha'),
                **chk)


def _face_vals(lin: np.ndarray, box, orig):
    """按机内 JPG 测到的脸框，从 RAW 的线性图里取脸上像素的 Y。

    box = [x, y, w, h]（原生坐标），orig = [W0, H0]；椭圆取法与测量层一致
    （0.42w × 0.55h），这样两边的「脸」是同一块地方。
    """
    if not box or not orig:
        return None
    h, w = np.asarray(lin).shape[:2]
    s = w / float(orig[0]) if orig[0] else 1.0
    x, y, bw, bh = [float(v) * s for v in box]
    if bw <= 1 or bh <= 1:
        return None
    cx, cy = x + bw / 2.0, y + bh / 2.0
    yy, xx = np.mgrid[0:h, 0:w]
    m = (((xx - cx) / (0.42 * bw)) ** 2 + ((yy - cy) / (0.55 * bh)) ** 2) <= 1.0
    if m.sum() < 50:
        return None
    yl = np.asarray(lin, np.float64) @ _Y_RGB
    return yl[m]


def pe_response(lin: np.ndarray, table: dict, pe: float, dlog: float = 0.3) -> float:
    """整幅中位对 PE 的局部响应：dL*/dlog2(PE)（负数：PE 越大越暗）。

    代理渲染那一步要用它把「脸差了多少 L*」换算成「PE 该动几档」。
    """
    pes, l2, logy, Lmat = _maps(table)
    x = _input_logy(lin)
    lo = float(np.log2(pe) - dlog)
    hi = float(np.log2(pe) + dlog)
    a = float(np.median(np.interp(x, logy, _row_at(Lmat, l2, lo))))
    b = float(np.median(np.interp(x, logy, _row_at(Lmat, l2, hi))))
    return (b - a) / (hi - lo)


def solve_pe_face(lin: np.ndarray, box, orig, target_L: float,
                  bias: float = FACE_BIAS, table: dict = None,
                  pe_nominal: Optional[float] = None,
                  span_ev: float = 4.0) -> dict:
    """**锚脸**：解 PE，让这张渲出来时「脸」落在 target_L。

    依据（746 张大师片实测 + 通用做法）：
      · 大师片 skin.L 中位 **70.6**、person.L 中位 **65.1**；人落在 55~75 时
        整幅 p5 中位 8.2、p95 中位 90.2 —— 这就是「暗部明部都正常」的样子；
      · 摄影上的通行做法也是「按脸测光」：入射式在脸的位置测、点测打脸、
        数码的人脸测光，都是把**主体**放到固定亮度上，背景随它去
        （逆光人像宁可背景过曝，也不让人脸欠曝）；
      · 所以曝光目标 = 脸的亮度，不是整幅中位（整幅中位由背景面积决定，
        高反差场景会把人抬到 90+，暗部跟着一起抬 → 灰）。

    「渲出来脸多亮」是从 RAW 里按脸框取样、再过曝光表算出来的（实测误差中位 5 L*，
    系统性偏低 FACE_BIAS，已补偿），**不需要先渲一张代理**。
    """
    table = table if table is not None else load_table()
    from .measure_all import skin_of_linear
    sk = skin_of_linear(np.asarray(lin, np.float64))
    if not sk.get('ok'):
        raise ValueError('solve_pe_face：取不到脸（%s）' % sk.get('why'))
    Lf = float(sk['L'])
    # 脸在**输出域**的亮度：显示 L* → 线性 Y（只做 sRGB 传输函数，所以一一对应）
    y_face = ((Lf + 16.0) / 116.0) ** 3
    vals = np.array([y_face], float)
    pes, l2, logy, Lmat = _maps(table)
    nom = float(pe_nominal if pe_nominal is not None else table.get('pe_nominal') or 1.0)
    xl = np.log10(np.clip(vals, 1e-7, None))

    def pred(log2pe: float) -> float:
        row = _row_at(Lmat, l2, log2pe)
        return float(np.median(np.interp(xl, logy, row))) + float(bias)

    lo = max(np.log2(nom) - span_ev, l2[0])
    hi = min(np.log2(nom) + span_ev, l2[-1])
    v_lo, v_hi = pred(lo), pred(hi)
    hit = False
    if target_L >= v_lo:
        best, hit = lo, True
    elif target_L <= v_hi:
        best, hit = hi, True
    else:
        a, b = lo, hi
        for _ in range(40):
            mid = 0.5 * (a + b)
            if pred(mid) > target_L:
                a = mid
            else:
                b = mid
        best = 0.5 * (a + b)
    row = _row_at(Lmat, l2, best)
    frame_vals = np.interp(_input_logy(np.asarray(lin, np.float64)), logy, row)
    q = {k: float(np.percentile(frame_vals, k)) for k in Q_KEYS}
    chk = _model_check(_input_logy(np.asarray(lin, np.float64)), logy, row)
    return dict(pe=round(float(2.0 ** best), 6), face_pred=round(pred(best), 1),
                face_target=round(float(target_L), 1),
                face_bias=round(float(bias), 1),
                p50_pred=round(q[50], 2),
                q_pred={k: round(v, 1) for k, v in q.items()},
                delta_ev=round(float(best - np.log2(nom)), 3),
                hit_bound=bool(hit), target=round(float(target_L), 2),
                pe_nominal=round(nom, 6), chart_sha=table.get('chart_sha'),
                **chk)


def solve_pe_person(lin: np.ndarray, person_L: float, gap: float,
                    table: dict, pe_nominal: Optional[float] = None,
                    span_ev: float = 4.0) -> dict:
    """**锚人**：解 PE，让这张渲出来时「人物」落在 person_L。

    为什么锚人、不锚中位（2026-11-03 实测 746 张大师片）：
      · 大师片里人物在整幅分布的第 64 百分位（中位），人比暗部高 ~56 级、
        高光比人高 ~25 级；人落在 55~75 时，整幅 p5 中位 8.2、p95 中位 90.2；
      · 而整幅中位是**背景面积**决定的：高反差场景（背景暗、人大）中位很低，
        拿它当靶会把人抬到 90+，暗部跟着一起抬 → 发灰；
      · 人总在光里（拍摄习惯）⇒ 人的亮度是这张图唯一可靠的锚。
    投影用「人和高光的距离」（同一张 JPG 上量的），不用百分位 —— 实测百分位在两套
    曲线之间不可搬（会把 4 张算成死黑）；距离只假设高光在两头都靠顶，稳得多。
    """
    pes, l2, logy, Lmat = _maps(table)
    nom = float(pe_nominal if pe_nominal is not None else table.get('pe_nominal') or 1.0)
    x = _input_logy(lin)
    g = float(gap)

    def pred(log2pe: float):
        row = _row_at(Lmat, l2, log2pe)
        vals = np.interp(x, logy, row)
        # 预测的「人」= 预测的高光 p95 − 这张图里「人离高光」的距离
        # （距离在同一张 JPG 上量，比百分位稳：不依赖两套曲线的形状）
        return float(np.percentile(vals, 95)) - g, vals, row

    lo = max(np.log2(nom) - span_ev, l2[0])
    hi = min(np.log2(nom) + span_ev, l2[-1])
    # PE 越大越暗 ⇒ pred 随 log2(PE) 单调下降
    v_lo = pred(lo)[0]
    v_hi = pred(hi)[0]
    hit = False
    if person_L >= v_lo:
        best, hit = lo, True
    elif person_L <= v_hi:
        best, hit = hi, True
    else:
        a, b = lo, hi
        for _ in range(40):
            mid = 0.5 * (a + b)
            if pred(mid)[0] > person_L:
                a = mid
            else:
                b = mid
        best = 0.5 * (a + b)
    pv, vals, row = pred(best)
    q = {k: float(np.percentile(vals, k)) for k in Q_KEYS}
    chk = _model_check(x, logy, row)
    return dict(pe=round(float(2.0 ** best), 6), person_pred=round(pv, 1),
                person_target=round(float(person_L), 1), person_gap=round(g, 1),
                p50_pred=round(q[50], 2),
                q_pred={k: round(v, 1) for k, v in q.items()},
                delta_ev=round(float(best - np.log2(nom)), 3),
                hit_bound=bool(hit), target=round(float(person_L), 2),
                pe_nominal=round(nom, 6), chart_sha=table.get('chart_sha'),
                **chk)


def solve_pe(lin: np.ndarray, target: float, table: dict,
             pe_nominal: Optional[float] = None, span_ev: float = 4.0) -> dict:
    """按输入亮度分布解 PE；不渲图。返回 {pe, p50_pred, delta_ev, hit_bound, model_ok…}。

    ★ 2026-11-03 加 `model_ok`：表只在**楔子覆盖的亮度段**里可信。比最暗一块
    还黑的像素会被 `np.interp` 钳在最暗那块的 L 上；一半以上的像素都进钳位区时，
    `p50_pred` 不再反映这张图的分布，只是"表的暗端"。这时解出来的 PE 会让片子
    **比 target 暗得多**（实测：target 33.3 → 渲出来 p50 12~16），所以必须记账，
    不许当成"已解到 target"。亮端同理。
    """
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

    def rows_at(log2pe: float):
        l2 = np.log2(pes)
        if log2pe <= l2[0]:
            return 0, 0, 0.0
        if log2pe >= l2[-1]:
            return len(l2) - 1, len(l2) - 1, 0.0
        i1 = int(np.searchsorted(l2, log2pe))
        i0 = i1 - 1
        return i0, i1, (log2pe - l2[i0]) / (l2[i1] - l2[i0])

    def row_at(log2pe: float) -> np.ndarray:
        """某个 PE 下的整条 y→L：**PE 方向走单调三次**，不是两点线性。

        暗端的 L(log2 PE) 曲得厉害（实测一条灰阶：55→20→4→1，每档跌幅 −35/−16/−3），
        线性插值会系统性偏亮：540 长边、PE=0.347 处线性 +2.9 L*，PCHIP +0.6 L*。
        表档数 ≥4 时用 PCHIP（Fritsch–Carlson，不越界、不反折），否则退线性。
        """
        l2 = np.log2(pes)
        if len(l2) >= 4:
            return np.array([_pchip(l2.tolist(), Lmat[:, j].tolist(), float(log2pe))
                             for j in range(Lmat.shape[1])], np.float64)
        i0, i1, w = rows_at(log2pe)
        return Lmat[i0] if i0 == i1 else (1.0 - w) * Lmat[i0] + w * Lmat[i1]

    def p50_at(log2pe: float) -> float:
        return float(np.median(np.interp(x, logy, row_at(log2pe))))

    lo = max(np.log2(nom) - span_ev, np.log2(pes[0]))
    hi = min(np.log2(nom) + span_ev, np.log2(pes[-1]))
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

    # ---- 模型有效性：中位落进钳位区 ⇒ p50_pred 是"表端"，不是这张图的中位
    below = float(np.mean(x < logy[0]))
    above = float(np.mean(x > logy[-1]))
    row = row_at(best)
    floor_L = float(row[0])
    ceil_L = float(row[-1])
    ok = True
    why = ''
    if below >= 0.5:
        ok = False
        why = ('暗端钳位：%.0f%% 的像素比楔子最暗一块还黑 ⇒ 预测 p50 只是表的暗端 '
               'L*%.1f，不是这张图的中位' % (100.0 * below, floor_L))
    elif above >= 0.5:
        ok = False
        why = ('亮端钳位：%.0f%% 的像素比楔子最亮一块还亮 ⇒ 预测 p50 只是表的亮端 '
               'L*%.1f' % (100.0 * above, ceil_L))
    return dict(pe=round(pe, 6), p50_pred=round(pred, 2),
                delta_ev=round(float(best - np.log2(nom)), 3),
                hit_bound=bool(hit), target=round(tgt, 2),
                pe_nominal=round(nom, 6), chart_sha=table.get('chart_sha'),
                model_ok=bool(ok), model_why=why,
                below_frac=round(below, 4), above_frac=round(above, 4),
                floor_L=round(floor_L, 2), ceil_L=round(ceil_L, 2))
